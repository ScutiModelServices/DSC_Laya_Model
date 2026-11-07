import time

import pytest
from fastapi.testclient import TestClient

from app import create_app
from registry import MODELS, Registry
from updates import Updates


class FakeEngine:
    def predict(self, text, questions, max_len=512):
        return {}


def make(cached="old", remote="new", package=("0.4.21", "0.4.21"), download=None, fail_remote=False, marker=None):
    """An Updates over a Registry of fake engines; `state` is the 'Hub', `downloaded` the repos fetched."""
    state = {"cached": cached}
    downloaded, loads = [], []

    def loader(checkpoint):
        loads.append(checkpoint)
        return FakeEngine()

    def remote_sha(repo):
        if fail_remote:
            raise OSError("no network")
        return remote

    def fetch(repo):
        downloaded.append(repo)
        state["cached"] = remote

    registry = Registry(loader)
    updates = Updates(registry, latest_package=lambda: package[1], installed=lambda: package[0],
                      remote_sha=remote_sha, cached_sha=lambda repo: state["cached"], download=download or fetch,
                      model_repos=lambda: ["org/laya"], marker=marker)
    return updates, registry, downloaded, loads


def test_check_reports_a_newer_package_and_newer_weights():
    updates, *_ = make(package=("0.4.21", "0.5.0"))
    status = updates.check()
    assert status["package"] == {"name": "laya", "installed": "0.4.21", "latest": "0.5.0", "update": True}
    assert status["weights"] == [{"repo": "org/laya", "cached": "old", "latest": "new", "update": True}]
    assert updates.status() is status


def test_check_failure_is_reported_not_raised():
    updates, *_ = make(fail_remote=True)
    assert updates.check()["error"] == "OSError: no network"


def test_update_downloads_stale_weights_and_reloads_the_loaded_models():
    updates, registry, downloaded, loads = make()
    registry.predict(MODELS[0], ("t", {}, 16))
    assert updates.update_weights()["weights"][0]["update"] is False
    assert downloaded == ["org/laya"]
    assert loads == [MODELS[0], MODELS[0]]  # loaded again; the model that was never loaded stays lazy


def test_update_with_nothing_new_downloads_nothing():
    updates, registry, downloaded, loads = make(cached="new")
    registry.predict(MODELS[0], None)
    updates.update_weights()
    assert downloaded == [] and loads == [MODELS[0]]


def test_update_refuses_when_the_check_failed():
    updates, _, downloaded, _ = make(fail_remote=True)
    with pytest.raises(RuntimeError, match="no network"):
        updates.update_weights()
    assert downloaded == []


def client(updates, offline=False, monkeypatch=None):
    if offline:
        monkeypatch.setenv("SERVICE_OFFLINE", "1")
    return TestClient(create_app(Registry(lambda c: FakeEngine()), "secret", updates=updates))


def test_update_routes_are_admin_only():
    updates, *_ = make()
    http = client(updates)
    for method, path in (("get", "/admin/updates"), ("post", "/admin/updates/check"), ("post", "/admin/updates/weights")):
        assert getattr(http, method)(path).status_code == 401
    assert http.get("/admin/updates", headers={"X-Internal-Token": "secret"}).json()["checked_at"] is None


def test_admin_checks_then_downloads():
    updates, _, downloaded, _ = make()
    http, admin = client(updates), {"X-Internal-Token": "secret"}
    assert http.post("/admin/updates/check", headers=admin).json()["weights"][0]["update"] is True
    body = http.post("/admin/updates/weights", headers=admin).json()
    assert body["weights"][0]["update"] is False and downloaded == ["org/laya"]


def test_a_failed_download_is_a_502_with_the_reason():
    def broken(repo):
        raise OSError("disk full")

    updates, *_ = make(download=broken)
    response = client(updates).post("/admin/updates/weights", headers={"X-Internal-Token": "secret"})
    assert response.status_code == 502 and "disk full" in response.json()["detail"]


def test_updates_are_off_when_offline(monkeypatch):
    http, admin = client(None, offline=True, monkeypatch=monkeypatch), {"X-Internal-Token": "secret"}
    assert http.get("/admin/updates", headers=admin).json() == {"enabled": False}
    assert http.post("/admin/updates/weights", headers=admin).status_code == 409


def test_the_background_check_runs_at_start():
    updates, *_ = make()
    with TestClient(create_app(Registry(lambda c: FakeEngine()), "secret", updates=updates, check_hours=1)):
        for _ in range(100):  # the check runs in a thread after start
            if updates.status()["checked_at"]:
                break
            time.sleep(0.02)
    assert updates.status()["checked_at"] is not None


def test_other_replicas_reload_after_a_download(tmp_path):
    marker = tmp_path / "volume" / "version"
    one, registry_one, downloaded, loads_one = make(marker=marker)
    two, registry_two, _, loads_two = make(marker=marker)
    registry_one.predict(MODELS[0], None)
    registry_two.predict(MODELS[0], None)
    assert not two.sync()
    note = one.update_weights()["reload_note"]
    assert "other replicas" in note and downloaded == ["org/laya"]
    assert one.sync() is False  # the replica that downloaded does not reload twice
    assert loads_one == [MODELS[0], MODELS[0]]
    assert two.sync() is True and loads_two == [MODELS[0], MODELS[0]]
    assert two.sync() is False and registry_two.generation == 1


def test_the_background_sync_reloads_a_replica(tmp_path):
    marker = tmp_path / "version"
    one, _, _, _ = make(marker=marker)
    two, registry_two, _, loads_two = make(marker=marker)
    registry_two.predict(MODELS[0], None)
    with TestClient(create_app(registry_two, "secret", updates=two, sync_seconds=0.05)):
        one.update_weights()
        for _ in range(100):
            if len(loads_two) == 2:
                break
            time.sleep(0.02)
    assert loads_two == [MODELS[0], MODELS[0]]


def test_the_check_interval_can_change_while_running(tmp_path):
    from options import SETTINGS
    from settings import Config
    config = Config(tmp_path / "s.json", SETTINGS)
    config.set({"update_check_hours": 0, "sync_seconds": 0})
    updates, registry, _, _ = make()
    with TestClient(create_app(registry, "secret", updates=updates, config=config)):
        time.sleep(0.3)
        assert updates.status()["checked_at"] is None
        config.set({"update_check_hours": 1})
        for _ in range(300):  # the loop looks again every second
            if updates.status()["checked_at"]:
                break
            time.sleep(0.02)
    assert updates.status()["checked_at"] is not None
