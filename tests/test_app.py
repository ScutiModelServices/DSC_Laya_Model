from fastapi.testclient import TestClient

from app import create_app
from registry import Registry

BODY = {"strategy": "english-only", "text": "hi", "max_len": 512,
        "questions": {"q": {"type": "noul", "instructions": "x"}}}


class FakeEngine:
    def predict(self, text, questions, max_len):
        return {"answers": {"q": {"noul": 0.5, "answer_confidence": 0.9}}, "usage": {"input_tokens": len(text)}}


def client(admin_password=None, loader=lambda checkpoint: FakeEngine()):
    return TestClient(create_app(Registry(loader), admin_password))


def test_ask_loads_a_model_once_and_reports_each_call():
    loads = []
    c = client(loader=lambda m: loads.append(m) or FakeEngine())
    body = c.post("/v1/ask", json=BODY).json()
    assert body["answers"]["q"]["noul"] == 0.5
    assert body["calls"][0]["model"] == "convaiinnovations/laya" and body["calls"][0]["input_tokens"] == 2
    c.post("/v1/ask", json=BODY)
    assert loads == ["convaiinnovations/laya"]
    assert c.get("/health").json()["models"] == {"convaiinnovations/laya": True, "typed-decisions": False}


def test_engine_failure_is_a_generic_500():
    class Broken:
        def predict(self, *args, **kwargs):
            raise RuntimeError("secret path C:/x")

    r = client(loader=lambda m: Broken()).post("/v1/ask", json=BODY)
    assert r.status_code == 500 and "secret" not in r.text


def test_a_failed_request_still_reports_the_calls_it_made():
    class Cut:
        def predict(self, *args, **kwargs):
            return {"answers": {}, "usage": {"truncated": True, "input_tokens": 9}}

    r = client(loader=lambda m: Cut()).post("/v1/ask", json=BODY)
    assert r.status_code == 413 and r.json()["detail"]["calls"][0]["ok"] is False


def test_thresholds_and_ratios_are_validated():
    c = client()
    assert c.post("/v1/ask", json={**BODY, "thresholds": {"4": {"laya-english": 0.5}}}).status_code == 422
    assert c.post("/v1/ask", json={**BODY, "prune_ratio": 1}).status_code == 422


def voter(pick, confidence, probabilities):
    return {"type": "choice", "choice": pick, "probabilities": probabilities, "answer_confidence": confidence}


def test_combine_restricts_averages_and_compares_the_voters():
    voters = {"laya-english": voter("a", 0.5, {"a": 0.5, "b": 0.45, "c": 0.05}),
              "jev": voter("b", 0.6, {"a": 0.2, "b": 0.7, "c": 0.1})}
    r = client().post("/v1/combine", json={"voters": voters, "options": ["a", "b", "c"]}).json()
    assert r["kept"] == ["a", "b"] and r["final"]["answered_by"] == "laya-english+jev" and r["final"]["uncertain"] is True
    narrowed = client().post("/v1/combine", json={"voters": voters, "options": ["a", "b"]}).json()
    assert abs(sum(narrowed["voters"]["jev"]["probabilities"].values()) - 1) < 1e-9


def test_combine_with_one_voter_has_no_final_and_bad_voters_are_422():
    one = {"jev": voter("a", 0.5, {"a": 0.5, "b": 0.5})}
    assert "final" not in client().post("/v1/combine", json={"voters": one}).json()
    assert client().post("/v1/combine", json={"voters": {"jev": {"type": "choice"}}}).status_code == 422


def test_validate_is_the_one_definition_of_a_valid_question_set():
    c = client()
    good = {"q": {"type": "choice", "instructions": "Which?", "criteria": {"a": "A", "b": "B"}}}
    assert c.post("/v1/validate", json={"questions": good}).json() == {"ok": True}
    bad = [
        {}, {f"q{i}": good["q"] for i in range(9)}, {"q": "oops"}, {"q": {**good["q"], "type": "multi"}},
        {"q": {**good["q"], "instructions": ""}}, {"q": {**good["q"], "extra": 1}},
        {"q": {**good["q"], "criteria": {"a": "only one"}}},
        {"q": {**good["q"], "criteria": {f"o{i}": "d" for i in range(11)}}},
        {"q": {**good["q"], "criteria": ["a", "b"]}}, {"q": {**good["q"], "criteria": {"a": 1, "b": "B"}}},
        {"q": {"type": "score", "instructions": "x", "criteria": {"a": "A", "b": "B"}}},
        {"q": {"type": "score", "instructions": "x", "criteria": ["only"]}},
        {"q": {"type": "noul", "instructions": "x", "criteria": {"yes": "y", "no": "n"}}}, [], "text",
    ]
    for questions in bad:
        assert c.post("/v1/validate", json={"questions": questions}).status_code == 422, questions
    message = c.post("/v1/validate", json={"questions": bad[6]}).json()["detail"]
    assert "criteria must be an object of 2 to 10 option: description strings" in message
    assert c.post("/v1/ask", json={**BODY, "questions": bad[3]}).status_code == 422


def test_each_project_gets_its_own_revocable_token(tmp_path):
    from tokens import TokenStore
    c = TestClient(create_app(Registry(lambda checkpoint: FakeEngine()), 'admin', tokens=TokenStore(tmp_path / 't.json')))
    path = "/v1/ask" if "LAYA" == "LAYA" else "/v1/chat"
    admin = {"X-Internal-Token": "admin"}
    assert c.get("/admin/tokens").status_code == 401
    made = c.post("/admin/tokens", json={"name": "proj"}, headers=admin).json()
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["last_used"] is None
    use = {"X-Internal-Token": made["token"]}
    assert c.post(path, json=BODY, headers=use).status_code == 200
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["last_used"] is not None
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["requests"] == 1
    assert c.get("/admin/tokens", headers=use).status_code == 401  # a project token is not an admin
    assert "token" not in c.get("/admin/tokens", headers=admin).json()["tokens"][0]
    assert c.delete(f"/admin/tokens/{made['id']}", headers=admin).status_code == 200
    assert c.post(path, json=BODY, headers=use).status_code == 401


def test_torch_threads_are_reported_and_set_by_the_admin(monkeypatch):
    seen = []
    monkeypatch.setattr("app._set_torch_threads", seen.append)
    monkeypatch.setattr("app._torch_threads", lambda: 3)
    c = TestClient(create_app(Registry(lambda checkpoint: FakeEngine()), 'admin'))
    assert c.get("/health").json()["torch_threads"] == 3
    assert c.put("/admin/threads", json={"torch_threads": 2}).status_code == 401
    assert c.put("/admin/threads", json={"torch_threads": 0}, headers={"X-Internal-Token": "admin"}).status_code == 422
    assert c.put("/admin/threads", json={"torch_threads": 2}, headers={"X-Internal-Token": "admin"}).json() == {"torch_threads": 3}
    assert seen == [2]



def test_workers_are_read_only_and_admin_only(monkeypatch):
    monkeypatch.setenv("LAYA_REPLICAS", "3")
    c = TestClient(create_app(Registry(lambda checkpoint: FakeEngine()), "admin"))
    assert c.get("/admin/workers").status_code == 401
    assert c.get("/admin/workers", headers={"X-Internal-Token": "admin"}).json() == {"replicas": 3}
    assert c.put("/admin/workers", json={"replicas": 5}, headers={"X-Internal-Token": "admin"}).status_code == 405


def test_main_prefers_saved_threads_over_environment(tmp_path, monkeypatch):
    import app
    import settings
    from tokens import TokenStore
    store = TokenStore(tmp_path / "tokens.json")
    settings.save(store.settings_path, torch_threads=7)
    monkeypatch.setenv("LAYA_TOKENS_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("LAYA_TORCH_THREADS", "2")
    seen = []
    monkeypatch.setattr(app, "_set_torch_threads", seen.append)
    monkeypatch.setattr(app, "create_app", lambda **kw: object())
    monkeypatch.setattr("uvicorn.run", lambda *args, **kw: None)
    app.main()
    assert seen == [7]


def test_health_ready_requires_all_models():
    registry = Registry(lambda checkpoint: FakeEngine())
    http = TestClient(create_app(registry))
    assert http.get("/health").json()["ready"] is False
    registry.predict(registry.models[0], None)
    assert http.get("/health").json()["ready"] is False
    registry.warm()
    body = http.get("/health").json()
    assert body["ready"] is True
    assert body["status"] == "ok" and all(body["models"].values())


def test_a_token_limited_to_projects_refuses_other_projects(tmp_path):
    from tokens import TokenStore
    c = TestClient(create_app(Registry(lambda checkpoint: FakeEngine()), "admin", tokens=TokenStore(tmp_path / "t.json")))
    admin = {"X-Internal-Token": "admin"}
    assert c.post("/admin/tokens", json={"name": "x", "projects": ["Bad Name"]}, headers=admin).status_code == 422
    made = c.post("/admin/tokens", json={"name": "p", "projects": ["app-b", "app-a"]}, headers=admin).json()
    assert made["projects"] == ["app-a", "app-b"]
    assert c.get("/admin/tokens", headers=admin).json()["tokens"][0]["projects"] == ["app-a", "app-b"]
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/ask", json={**BODY, "project": "app-a"}, headers=use).status_code == 200
    assert c.post("/v1/ask", json={**BODY, "project": "other"}, headers=use).status_code == 403
    assert c.post("/v1/ask", json=BODY, headers=use).status_code == 200  # no project is always allowed
    open_token = c.post("/admin/tokens", json={"name": "all"}, headers=admin).json()["token"]
    assert c.post("/v1/ask", json={**BODY, "project": "other"}, headers={"X-Internal-Token": open_token}).status_code == 200


def test_rate_limit_edit_stats_and_metrics(tmp_path):
    from tokens import TokenStore
    store = TokenStore(tmp_path / "t.json")
    c = TestClient(create_app(Registry(lambda checkpoint: FakeEngine()), "admin", tokens=store))
    admin = {"X-Internal-Token": "admin"}
    made = c.post("/admin/tokens", json={"name": "p", "rate": 1}, headers=admin).json()
    assert made["rate"] == 1
    use = {"X-Internal-Token": made["token"]}
    assert c.post("/v1/ask", json=BODY, headers=use).status_code == 200
    limited = c.post("/v1/ask", json=BODY, headers=use)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert c.put(f"/admin/tokens/{made['id']}", json={"projects": ["a"], "rate": None}, headers=admin).status_code == 200
    assert c.post("/v1/ask", json=BODY, headers=use).status_code == 200  # the limit is gone
    assert c.put("/admin/tokens/nope", json={}, headers=admin).status_code == 404
    row = store.list()[0]
    assert row["projects"] == ["a"] and "rate" not in row and row["timed"] >= 1
    assert c.get("/metrics").status_code == 401
    assert 'laya_requests_total{token="p"}' in c.get("/metrics", headers=admin).text


def test_identical_questions_are_answered_from_the_cache_until_the_weights_change():
    calls = []

    class Counting(FakeEngine):
        def predict(self, text, questions, max_len):
            calls.append(text)
            return super().predict(text, questions, max_len)

    registry = Registry(lambda checkpoint: Counting())
    c = TestClient(create_app(registry, cache_size=2))
    first = c.post("/v1/ask", json=BODY)
    assert first.headers["X-Laya-Cache"] == "miss"
    again = c.post("/v1/ask", json=BODY)
    assert again.headers["X-Laya-Cache"] == "hit" and again.json() == first.json() and len(calls) == 1
    c.post("/v1/ask", json={**BODY, "text": "other"})
    c.post("/v1/ask", json={**BODY, "text": "third"})  # size 2: the first answer is gone
    assert c.post("/v1/ask", json=BODY).headers["X-Laya-Cache"] == "miss"
    registry.reload()
    assert c.post("/v1/ask", json=BODY).headers["X-Laya-Cache"] == "miss"
    assert "X-Laya-Cache" not in TestClient(create_app(Registry(lambda m: FakeEngine()))).post("/v1/ask", json=BODY).headers
