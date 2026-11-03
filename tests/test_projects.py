"""Project routing uses local artifacts without changing the base strategy engines."""

import os
from concurrent.futures import ThreadPoolExecutor
import threading

from fastapi.testclient import TestClient
import pytest

from app import create_app
from registry import MODELS, Registry, _REQUIRED_FILES
from test_app import BODY, FakeEngine


def checkpoint(root, project):
    path = root / project
    for name in _REQUIRED_FILES:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    return str(path.resolve())


def predict_project(registry, project, call):
    with registry.project_model(project) as (model, fallback):
        return registry.predict(model, call), model, fallback


def configured(tmp_path, monkeypatch, cap=2):
    monkeypatch.setenv("LAYA_PROJECT_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("LAYA_MAX_PROJECT_MODELS", str(cap))
    loads = []
    registry = Registry(lambda model: loads.append(model) or FakeEngine())
    return registry, TestClient(create_app(registry)), loads


def test_project_replaces_typed_and_reports_actual_model(tmp_path, monkeypatch):
    path = checkpoint(tmp_path, "my-project_2")
    registry, http, loads = configured(tmp_path, monkeypatch)
    body = http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": "my-project_2"}).json()
    assert body["model"] == path and body["fallback"] is False
    assert body["calls"][0]["model"] == path and body["calls"][0]["fallback"] is False
    assert loads == [path]
    assert http.get("/health").json()["project_models"] == {"my-project_2": path}
    english = http.post("/v1/ask", json={**BODY, "project": "my-project_2"}).json()
    assert english["model"] == MODELS[0] and loads == [path, MODELS[0]]


@pytest.mark.parametrize("project", ["missing", "incomplete"])
def test_missing_or_incomplete_checkpoint_falls_back(tmp_path, monkeypatch, project):
    (tmp_path / "incomplete").mkdir()
    _, http, loads = configured(tmp_path, monkeypatch)
    body = http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": project}).json()
    assert body["model"] == "typed-decisions" and body["fallback"] is True
    assert body["calls"][0]["fallback"] is True and loads == ["typed-decisions"]


def test_unset_root_disables_projects_and_absent_project_is_not_fallback(tmp_path, monkeypatch):
    checkpoint(tmp_path, "demo")
    monkeypatch.delenv("LAYA_PROJECT_MODELS_DIR", raising=False)
    http = TestClient(create_app(Registry(lambda model: FakeEngine())))
    for extra, fallback in [({"project": "demo"}, True), ({}, False)]:
        body = http.post("/v1/ask", json={**BODY, "strategy": "typed-only", **extra}).json()
        assert body["model"] == "typed-decisions" and body["fallback"] is fallback


@pytest.mark.parametrize("project", ["../x", "", "Upper", "a" * 49, "x\n", "_x", "a/b"])
def test_invalid_projects_rejected_before_loading(project):
    loads = []
    http = TestClient(create_app(Registry(lambda model: loads.append(model) or FakeEngine())))
    assert http.post("/v1/ask", json={**BODY, "project": project}).status_code == 422
    assert loads == []


def test_registry_validates_project_before_filesystem(tmp_path, monkeypatch):
    registry, _, loads = configured(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="project"):
        predict_project(registry, "../x", None)
    assert loads == []


def test_lru_keeps_recent_projects_and_never_evicts_base(tmp_path, monkeypatch):
    paths = {p: checkpoint(tmp_path, p) for p in ("a", "b", "c")}
    registry, http, loads = configured(tmp_path, monkeypatch)
    registry.warm()
    for project in ("a", "b", "a", "c"):
        assert http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": project}).status_code == 200
    health = http.get("/health").json()
    assert health["project_models"] == {"a": paths["a"], "c": paths["c"]}
    assert all(health["models"].values())
    http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": "b"})
    assert loads.count(paths["b"]) == 2
    registry.reload()
    assert loads.count(paths["c"]) == 1  # Hub update reloads base engines only.
    assert loads.count(MODELS[0]) == 2 and loads.count(MODELS[1]) == 2


def test_busy_project_is_not_evicted_or_loaded_twice(tmp_path, monkeypatch):
    checkpoint(tmp_path, "a")
    checkpoint(tmp_path, "b")
    monkeypatch.setenv("LAYA_PROJECT_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("LAYA_MAX_PROJECT_MODELS", "1")
    entered, release = threading.Event(), threading.Event()
    loads = []

    class Busy(FakeEngine):
        def predict(self, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().predict(*args, **kwargs)

    registry = Registry(lambda model: loads.append(model) or Busy())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(predict_project, registry, "a", ("hi", BODY["questions"], 512))
        try:
            assert entered.wait(5)
            second = pool.submit(predict_project, registry, "b", None)
            assert len(loads) == 1
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert len(registry.loaded_projects()) == 1


def test_offline_local_checkpoint_never_uses_hub_cache(tmp_path, monkeypatch):
    import registry as module
    import sys
    from types import SimpleNamespace

    path = checkpoint(tmp_path, "demo")
    monkeypatch.setenv("LAYA_PROJECT_MODELS_DIR", str(tmp_path))
    loaded = []
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda name: loaded.append(name) or FakeEngine()))
    monkeypatch.setattr(module, "cached_checkpoint", lambda name: pytest.fail("Local checkpoint reached Hub cache"))
    predict_project(Registry(offline=True), "demo", None)
    assert loaded == [path]


def test_project_load_value_error_keeps_english_recovery_and_call_metadata(tmp_path, monkeypatch):
    path = checkpoint(tmp_path, "broken")
    monkeypatch.setenv("LAYA_PROJECT_MODELS_DIR", str(tmp_path))

    def loader(model):
        if model == path:
            raise ValueError("Invalid local checkpoint")
        return FakeEngine()

    http = TestClient(create_app(Registry(loader)))
    response = http.post("/v1/ask", json={**BODY, "strategy": "typed-then-english", "project": "broken"})
    assert response.status_code == 200
    assert response.json()["model"] == MODELS[0]
    calls = response.json()["calls"]
    assert calls[0]["model"] == path and calls[0]["ok"] is False and calls[0]["fallback"] is False
    assert calls[1]["model"] == MODELS[0] and calls[1]["ok"] is True
    assert http.get("/health").json()["project_models"] == {}


def test_project_directory_aliases_are_refused(tmp_path, monkeypatch):
    from pathlib import Path

    path = checkpoint(tmp_path, "a")
    registry, http, loads = configured(tmp_path, monkeypatch)
    original = Path.resolve

    def resolve(candidate, *args, **kwargs):
        # Simulate a directory symlink without requiring Windows symlink privileges.
        return Path(path) if candidate == tmp_path / "alias" else original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    body = http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": "alias"}).json()
    assert body["model"] == MODELS[1] and body["fallback"] is True
    assert loads == [MODELS[1]]


def test_project_used_is_false_when_the_strategy_never_asks_typed(tmp_path, monkeypatch):
    path = checkpoint(tmp_path, "demo")
    _, http, _ = configured(tmp_path, monkeypatch)
    english = http.post("/v1/ask", json={**BODY, "strategy": "english-only", "project": "demo"}).json()
    assert english["project_used"] is False and english["fallback"] is False
    typed = http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": "demo"}).json()
    assert typed["project_used"] is True and typed["model"] == path
    missing = http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": "nope"}).json()
    assert missing["project_used"] is False and missing["fallback"] is True


def test_all_slots_busy_past_the_wait_limit_is_a_503_not_a_hang(tmp_path, monkeypatch):
    checkpoint(tmp_path, "a")
    checkpoint(tmp_path, "b")
    monkeypatch.setenv("LAYA_PROJECT_WAIT_SECONDS", "0.1")
    registry, http, _ = configured(tmp_path, monkeypatch, cap=1)
    with registry.project_model("a"):
        response = http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": "b"})
    assert response.status_code == 503 and response.headers["retry-after"] == "5"
    assert http.post("/v1/ask", json={**BODY, "strategy": "typed-only", "project": "b"}).status_code == 200


def test_negative_wait_is_refused(monkeypatch):
    monkeypatch.setenv("LAYA_PROJECT_WAIT_SECONDS", "-1")
    with pytest.raises(ValueError):
        Registry(lambda model: FakeEngine())


def test_replaced_checkpoint_files_are_loaded_again_once_idle(tmp_path, monkeypatch):
    path = checkpoint(tmp_path, "demo")
    weights = tmp_path / "demo" / "model.safetensors"
    registry, _, loads = configured(tmp_path, monkeypatch)
    call = ("hi", {}, 16)
    predict_project(registry, "demo", call)
    predict_project(registry, "demo", call)
    assert loads == [path]  # unchanged files: one load
    os.utime(weights, ns=(10**9, 10**9))
    with registry.project_model("demo") as (model, _):  # replaced while in use: the leased engine stays
        registry.predict(model, call)
        os.utime(weights, ns=(2 * 10**9, 2 * 10**9))
        with registry.project_model("demo") as (again, _):
            registry.predict(again, call)
    assert loads == [path, path]  # reloaded by the lease that found it idle after the first utime
    predict_project(registry, "demo", call)
    assert loads == [path, path, path]  # the second replacement is picked up once nothing holds it


def test_cache_is_not_reused_after_project_files_are_replaced(tmp_path, monkeypatch):
    checkpoint(tmp_path, "demo")
    monkeypatch.setenv("LAYA_PROJECT_MODELS_DIR", str(tmp_path))
    http = TestClient(create_app(Registry(lambda model: FakeEngine()), cache_size=4))
    body = {**BODY, "strategy": "typed-only", "project": "demo"}
    assert http.post("/v1/ask", json=body).headers["X-Laya-Cache"] == "miss"
    assert http.post("/v1/ask", json=body).headers["X-Laya-Cache"] == "hit"
    os.utime(tmp_path / "demo" / "model.safetensors", ns=(5 * 10**9, 5 * 10**9))
    assert http.post("/v1/ask", json=body).headers["X-Laya-Cache"] == "miss"
