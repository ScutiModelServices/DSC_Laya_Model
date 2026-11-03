"""Browser smoke test for /admin/: sign in, create a token, revoke it.

Needs `pip install playwright && playwright install chromium`; skipped when Playwright is missing.
"""
import socket
import threading
import time

import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")

from app import create_app
from registry import Registry

PASSWORD = "secret"


@pytest.fixture
def base_url():
    app = create_app(Registry(lambda checkpoint: None), PASSWORD)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
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


def test_token_can_be_limited_to_projects(page):
    page.fill("#admin", PASSWORD)
    page.click("#signin-button")
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    page.fill("#name", "Garrison")
    page.fill("#projects", "Bad Name")
    page.click("#create-button")
    sync_api.expect(page.locator("#token-error")).to_contain_text("not valid")
    page.fill("#projects", "app-a, app-b")
    page.click("#create-button")
    sync_api.expect(page.locator("#rows tr")).to_contain_text("Only: app-a, app-b")
