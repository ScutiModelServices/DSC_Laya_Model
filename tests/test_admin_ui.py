"""Browser smoke test for /admin/: sign in, create a token, revoke it.

Needs `pip install playwright && playwright install chromium`; skipped when Playwright is missing.
"""
import socket
import threading
import time

import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")

import os
from pathlib import Path

from app import create_app
from options import SETTINGS
from settings import Config
from registry import Registry

PASSWORD = "secret"


class PlaygroundEngine:
    """Deterministic inference behind the ordinary public handler."""

    def predict(self, text, questions, max_len):
        if text == "__slow__":
            time.sleep(2)  # Registry executes inference on a worker, never on its event loop.
        return {"answers": {key: {"noul": 0.5, "answer_confidence": 0.9} for key in questions},
                "usage": {"input_tokens": len(text)}}


@pytest.fixture
def base_url():
    config = Config(Path(os.environ["SERVICE_TOKENS_FILE"]).with_name("settings.json"), SETTINGS)
    config.set({"update_check_hours": 0, "sync_seconds": 0, "warm": False})
    app = create_app(Registry(lambda checkpoint: PlaygroundEngine(), config=config), PASSWORD, config=config)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= deadline:
            server.should_exit = True
            pytest.fail("Browser test server did not start within 15 seconds")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/admin/"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def page(base_url):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base_url)
        yield page
        browser.close()


def test_wrong_password_is_rejected(page):
    page.fill("#admin", "nope")
    page.click("#signin-button")
    sync_api.expect(page.locator("#login-error")).not_to_be_empty()
    assert page.locator("#dashboard").is_hidden()


def test_create_and_revoke_a_token(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    sync_api.expect(page.locator("#empty")).to_be_visible()
    page.fill("#name", "Garrison")
    page.click("#create-button")
    sync_api.expect(page.locator("#fresh-token")).to_contain_text("laya_")
    row = page.locator("#rows tr")
    sync_api.expect(row).to_contain_text("Garrison")
    sync_api.expect(row.locator('td[data-label="Requests"]')).to_have_text("0")
    row.get_by_role("button", name="Revoke").click()
    page.click("#confirm-revoke")
    sync_api.expect(page.locator("#empty")).to_be_visible()


def test_health_keeps_backend_specific_model_display(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#model-value")).to_have_text("0 / 2")
    sync_api.expect(page.locator("#health-text")).to_have_text("Healthy")
    sync_api.expect(page.locator("#readiness-title")).to_have_text("Model not ready")


def test_token_can_be_limited_to_projects(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    page.fill("#name", "Garrison")
    page.fill("#projects", "Bad Name")
    page.click("#create-button")
    sync_api.expect(page.locator("#token-error")).to_contain_text("not valid")
    page.fill("#projects", "app-a, app-b")
    page.click("#create-button")
    sync_api.expect(page.locator("#rows tr")).to_contain_text("Only: app-a, app-b")


def test_token_can_be_edited_and_shows_rate_and_expiry(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    page.click('#admin-nav a[href="#models"]')
    sync_api.expect(page.locator("#project-models-empty")).to_be_visible()
    page.click('#admin-nav a[href="#tokens"]')
    page.fill("#name", "Garrison")
    page.fill("#rate", "5")
    page.fill("#expires", "30")
    page.click("#create-button")
    row = page.locator("#rows tr")
    sync_api.expect(row).to_contain_text("5/min")
    sync_api.expect(row).to_contain_text("Expires")
    row.get_by_role("button", name="Edit").click()
    page.fill("#edit-projects", "app-a")
    page.fill("#edit-rate", "")
    page.click("#save-edit")
    sync_api.expect(row).to_contain_text("Only: app-a")
    sync_api.expect(row).not_to_contain_text("5/min")


def test_settings_can_be_saved_and_reset(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#runtime"]')
    sync_api.expect(page.locator("#settings-card")).to_be_visible()
    box = page.locator("#setting-cache_size")
    sync_api.expect(box).to_have_value("256")
    box.fill("32")
    page.click("#save-settings")
    sync_api.expect(page.locator("#settings-note")).to_have_text("Saved")
    sync_api.expect(page.locator("#setting-cache_size")).to_have_value("32")
    page.get_by_role("button", name="Reset Answers kept per replica").click()
    sync_api.expect(page.locator("#setting-cache_size")).to_have_value("256")


def _open_playground(page):
    """Use separate admin and public credentials."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#playground"]')
    sync_api.expect(page.locator("#playground")).to_be_visible()
    made = page.request.post(page.url.split("#", 1)[0].rstrip("/") + "/tokens",
                             headers={"X-Internal-Token": PASSWORD}, data={"name": "playground-browser"})
    assert made.status == 200
    token = made.json()["token"]
    page.fill("#pg-token", token)
    return token


def _assert_playground_finished(page, token):
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP 200", timeout=15000)
    sync_api.expect(page.locator("#pg-send")).to_be_enabled(timeout=15000)
    assert "<SERVICE_TOKEN>" in page.locator("#pg-curl").text_content()
    assert token not in page.locator("#pg-curl").text_content()
    assert PASSWORD not in page.locator("#pg-curl").text_content()
    stored = page.evaluate("() => [...Object.values(localStorage), ...Object.values(sessionStorage)]")
    assert not any(token in value for value in stored)


def test_playground_uses_public_route_and_renders_answer(page):
    """Exercise real public auth and the family fake engine."""
    token = _open_playground(page)
    page.fill("#pg-text", "hi")
    page.fill("#pg-questions", '{"q":{"type":"noul","instructions":"x"}}')
    page.select_option("#pg-strategy", "english-only")
    page.click("#pg-send")
    _assert_playground_finished(page, token)
    sync_api.expect(page.locator("#pg-output")).to_contain_text("0.5")


def _fill_laya_playground(page, text="hi"):
    page.fill("#pg-text", text)
    page.fill("#pg-questions", '{"q":{"type":"noul","instructions":"x"}}')
    page.select_option("#pg-strategy", "english-only")


def test_playground_invalid_public_token_does_not_sign_out_admin(page):
    _open_playground(page)
    _fill_laya_playground(page)
    page.fill("#pg-token", "invalid-public-token")
    page.click("#pg-send")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP 401")
    sync_api.expect(page.locator("#pg-output")).to_contain_text("Invalid token")
    sync_api.expect(page.locator("#dashboard")).to_be_visible()


@pytest.mark.parametrize("status", [422, 429, 503])
def test_playground_error_body_is_visible_and_credentials_are_redacted(page, status):
    import json

    token = _open_playground(page)
    _fill_laya_playground(page)
    page.route("**/v1/ask", lambda route: route.fulfill(
        status=status, content_type="application/json", headers={"Retry-After": "5"},
        body=json.dumps({"detail": "Try later " + token + " " + PASSWORD}),
    ))
    page.click("#pg-send")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP " + str(status))
    sync_api.expect(page.locator("#pg-output")).to_contain_text("Try later")
    assert token not in page.locator("#pg-output").inner_text()
    assert PASSWORD not in page.locator("#pg-output").inner_text()
    assert token not in page.locator("#pg-curl").text_content()


def test_playground_cancel_aborts_public_fetch(page):
    _open_playground(page)
    _fill_laya_playground(page, "__slow__")
    page.click("#pg-send")
    sync_api.expect(page.locator("#pg-cancel")).to_be_enabled()
    page.click("#pg-cancel")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("Cancelled")
    sync_api.expect(page.locator("#pg-send")).to_be_enabled()


def test_playground_renders_model_markup_as_text_and_clears_on_signout(page):
    token = _open_playground(page)
    _fill_laya_playground(page)
    markup = '<img src=x onerror="window.playgroundInjected=true">'
    page.route("**/v1/ask", lambda route: route.fulfill(
        status=200, content_type="application/x-ndjson",
        body='{"type":"answer","content":' + __import__("json").dumps(markup) + '}\n',
    ))
    page.click("#pg-send")
    _assert_playground_finished(page, token)
    sync_api.expect(page.locator("#pg-output")).to_contain_text("onerror")
    assert page.locator("#pg-output img").count() == 0
    assert not page.evaluate("() => Boolean(window.playgroundInjected)")
    page.click("#signout")
    assert page.locator("#pg-token").input_value() == ""
    assert page.locator("#pg-output").inner_text() == ""


def test_playground_rejects_oversized_output_and_remains_usable(page):
    _open_playground(page)
    _fill_laya_playground(page)
    page.route("**/v1/ask", lambda route: route.fulfill(
        status=200, content_type="application/x-ndjson", body="x" * (256 * 1024 + 1),
    ))
    page.click("#pg-send")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("limit")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP 200")
    sync_api.expect(page.locator("#pg-send")).to_be_enabled()
    assert len(page.locator("#pg-output").inner_text()) <= 256 * 1024
    page.click("#pg-reset")
    assert page.locator("#pg-token").input_value() == ""
    assert page.locator("#pg-curl").text_content() == ""


@pytest.mark.parametrize("operation, expected", [("1", '"ok": true'), ("2", '"choice": "a"'), ("3", "english-only")])
def test_playground_auxiliary_operations_have_valid_defaults(page, operation, expected):
    token = _open_playground(page)
    page.select_option("#pg-operation", operation)
    page.click("#pg-send")
    _assert_playground_finished(page, token)
    sync_api.expect(page.locator("#pg-output")).to_contain_text(expected)


def test_playground_redacts_json_escaped_credential(page):
    import json

    _open_playground(page)
    _fill_laya_playground(page)
    credential = 'quote"slash\\secret'
    page.evaluate("value => { playground.options.secrets = () => [value]; }", credential)
    page.route("**/v1/ask", lambda route: route.fulfill(
        status=422, content_type="application/json", body=json.dumps({"detail": credential}),
    ))
    page.click("#pg-send")
    sync_api.expect(page.locator("#pg-status")).to_contain_text("HTTP 422")
    output = page.locator("#pg-output").inner_text()
    assert credential not in output
    assert json.dumps(credential)[1:-1] not in output
    assert "[REDACTED]" in output


def test_playground_secret_matching_static_route_does_not_block_request(page):
    token = _open_playground(page)
    _fill_laya_playground(page)
    page.evaluate("() => { playground.options.secrets = () => ['ask']; }")
    page.click("#pg-send")
    _assert_playground_finished(page, token)


def test_playground_stream_never_exposes_clipped_credential_prefix(page):
    _open_playground(page)
    snapshots = page.evaluate("""async () => {
        playground.token.value = "";
        playground.options.secrets = () => ["abcdefghij"];
        const snapshots = [];
        const observer = new MutationObserver(() => snapshots.push(playground.output.textContent));
        observer.observe(playground.output, {childList: true, characterData: true, subtree: true});
        const response = new Response(new ReadableStream({
            start(controller) {
                controller.enqueue(new TextEncoder().encode("abcdefghijxy"));
                setTimeout(() => controller.close(), 80);
            }
        }), {headers: {"Content-Type": "application/x-ndjson"}});
        await playground.read(response, "stream", new AbortController().signal, playground.version);
        await new Promise(resolve => setTimeout(resolve, 0));
        observer.disconnect();
        return snapshots;
    }""")
    assert snapshots
    assert all("ab" not in snapshot for snapshot in snapshots)
    assert snapshots[-1] == "[REDACTED]xy"


def test_playground_large_json_curl_uses_downloadable_request_without_expanding_display(page):
    import json

    token = _open_playground(page)
    _fill_laya_playground(page)
    questions = {"q": {"type": "noul", "instructions": "x" * 80000}}
    page.fill("#pg-questions", json.dumps(questions))
    page.route("**/v1/ask", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"ok": true}',
    ))
    page.click("#pg-send")
    _assert_playground_finished(page, token)
    assert len(page.locator("#pg-curl").text_content()) < 256 * 1024
    assert "@request-1.json" in page.locator("#pg-curl").text_content()
    link = page.locator("#pg-request-downloads a")
    assert link.get_attribute("href").startswith("blob:")
    saved = page.evaluate("async () => await (await fetch(document.querySelector('#pg-request-downloads a').href)).json()")
    assert saved["questions"] == questions
    page.click("#pg-reset")
    assert link.count() == 0


def test_playground_pretty_json_cutoff_never_exposes_credential_prefix(page):
    _open_playground(page)
    ending = page.evaluate("""async () => {
        const secret = 'abcdefghij', limit = 256 * 1024;
        playground.token.value = secret;
        playground.options.secrets = () => [];
        const position = JSON.stringify({pad: '', secret}, null, 2).indexOf(secret);
        const data = {pad: 'x'.repeat(limit - 5 - position), secret};
        const response = new Response(JSON.stringify(data), {headers: {'Content-Type': 'application/json'}});
        await playground.read(response, 'json', new AbortController().signal, playground.version);
        return playground.output.textContent.slice(-30);
    }""")
    assert "abcde" not in ending


def test_connect_card_lists_the_addresses_other_services_can_use(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    page.click('#admin-nav a[href="#connections"]')
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    card = page.locator("#connect-card")
    sync_api.expect(card).to_contain_text("The address you opened this page with")
    sync_api.expect(card).to_contain_text("host.docker.internal")
    sync_api.expect(card).to_contain_text("/health")
    port = page.url.split(":")[2].split("/")[0]
    sync_api.expect(card).to_contain_text("http://127.0.0.1:" + port)


def test_sidebar_pages_keep_one_header_and_preserve_form_drafts(page):
    """Navigation changes the visible page without rebuilding active forms."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#page-title")).to_have_text("Overview")
    sync_api.expect(page.locator("#create")).to_be_hidden()
    page.click('#admin-nav a[href="#tokens"]')
    page.fill("#name", "Keep this draft")
    page.click("#sidebar-toggle")
    sync_api.expect(page.locator("#sidebar-toggle")).to_have_attribute("aria-expanded", "false")
    sync_api.expect(page.locator("#name")).to_have_value("Keep this draft")
    page.click('#admin-nav a[href="#overview"]')
    page.click('#admin-nav a[href="#tokens"]')
    sync_api.expect(page.locator("#name")).to_have_value("Keep this draft")
    sync_api.expect(page.locator("#page-title")).to_have_text("Access tokens")
    assert page.locator("#dashboard h1").count() == 1
    page.reload()
    sync_api.expect(page.locator("#page-title")).to_have_text("Access tokens")
    sync_api.expect(page.locator("#sidebar-toggle")).to_have_attribute("aria-expanded", "false")


def test_sidebar_capabilities_and_history(page):
    """Unsupported pages cannot be opened through a manually entered hash."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    tags = page.evaluate("CONFIG.tags")
    assert page.locator('#admin-nav a[href="#models"]').count() == int("project-models" in tags)
    assert page.locator('#admin-nav a[href="#voices"]').count() == int("voice-cloning" in tags)
    page.click('#admin-nav a[href="#connections"]')
    sync_api.expect(page.locator("#page-title")).to_have_text("Connections")
    page.go_back()
    sync_api.expect(page.locator("#page-title")).to_have_text("Overview")
    page.go_forward()
    sync_api.expect(page.locator("#page-title")).to_have_text("Connections")
    page.evaluate("location.hash = '#unsupported'")
    sync_api.expect(page.locator("#page-title")).to_have_text("Overview")


def test_sidebar_responsive_layout_and_keyboard_toggle(page):
    """Both sidebar states retain navigation without horizontal page overflow."""
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    for width in (1280, 768, 375, 320):
        page.set_viewport_size({"width": width, "height": 900})
        for expanded in (False, True):
            page.locator("#sidebar-toggle").focus()
            page.keyboard.press("Enter")
            sync_api.expect(page.locator("#sidebar-toggle")).to_have_attribute("aria-expanded", str(expanded).lower())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator("#admin-sidebar").bounding_box()["x"] < page.locator("#page-content").bounding_box()["x"]
