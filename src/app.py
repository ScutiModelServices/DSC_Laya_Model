"""HTTP face of the Laya models.

POST /v1/ask {"strategy", "text", "questions", ...} answers typed questions with the chosen decision
strategy (see strategies.py); callers pick a strategy, never a model.
POST /v1/validate checks a question set against the one set of rules.
POST /v1/combine mixes a caller's own voter (Garrison's cloud fallback) into the local models' answers.
GET /v1/strategies lists the strategies; GET /health reports which models are loaded."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Annotated, Any, Callable, Literal

import anyio.to_thread
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import strategies
from cache import AnswerCache
from answers import InputTooLong, check_questions
from registry import PROJECT_PATTERN, ProjectBusy, Registry
import settings
from options import SETTINGS
from tokens import LatencyHistogram, TokenStore
from updates import Updates, default_marker

_log = logging.getLogger(__name__)

StrategyName = Literal[tuple(strategies.STRATEGIES)]  # type: ignore[valid-type]
_Thresholds = dict[Literal["2", "3+"], dict[Literal["laya-english", "laya-typed"], float]]


class AskRequest(BaseModel):
    project: str | None = Field(None, pattern=PROJECT_PATTERN)
    strategy: StrategyName = strategies.DEFAULT_STRATEGY
    text: str = Field(min_length=1, max_length=100_000)
    questions: dict[str, Any]
    max_len: int = Field(512, ge=16, le=8192)
    min_confidence: float | None = Field(None, gt=0, le=1)
    thresholds: _Thresholds = Field(default_factory=dict)
    prune_ratio: float = Field(strategies.PRUNE_RATIO, ge=0, lt=1)
    pair_ratio: float = Field(strategies.PAIR_RATIO, gt=0, le=1)
    trace: bool = False
    stream: bool = False  # true: each question's answer arrives as a JSON line when it is settled


class ValidateRequest(BaseModel):
    questions: Any


class CombineRequest(BaseModel):
    voters: dict[str, dict[str, Any]] = Field(min_length=1, max_length=4)
    options: list[str] | None = Field(None, min_length=2, max_length=10)
    prune_ratio: float = Field(strategies.PRUNE_RATIO, ge=0, lt=1)
    pair_ratio: float = Field(strategies.PAIR_RATIO, gt=0, le=1)


class SettingsUpdate(BaseModel):
    values: dict[str, Any]  # None resets an option to its environment variable or default


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    projects: list[Annotated[str, Field(pattern=PROJECT_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)  # calls per minute
    expires_days: int | None = Field(None, ge=1, le=3650)


class TokenScope(BaseModel):
    projects: list[Annotated[str, Field(pattern=PROJECT_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)


class Threads(BaseModel):
    torch_threads: int = Field(ge=1, le=256)


def _torch_threads() -> int | None:
    try:
        import torch
    except ImportError:
        return None
    return torch.get_num_threads()


def _set_torch_threads(threads: int) -> None:
    """In a container with a CPU quota torch still counts the host's cores, and more threads than quota makes each
    prediction several times slower. SERVICE_TORCH_THREADS sets this at start; /admin/threads changes it while running."""
    import torch

    torch.set_num_threads(threads)


def create_app(registry: Registry | None = None, admin_password: str | None = None, warm: bool = False,
               tokens: TokenStore | None = None, updates: Updates | None = None, check_hours: float = 0,
               cache_size: int = 0, sync_seconds: float = 0, max_inflight: int | None = None,
               config: settings.Config | None = None) -> FastAPI:
    """`admin_password` (SERVICE_ADMIN_PASSWORD) unlocks /admin/, where each project gets a token of its own; when it
    is set, every call except /health must send one of those tokens in the X-Internal-Token header. Unset = no
    auth at all (local use only). `tokens` holds the project tokens (SERVICE_TOKENS_FILE). `check_hours` > 0 looks for
    newer Laya releases that often (SERVICE_UPDATE_CHECK_HOURS); it never downloads. `updates` is off with SERVICE_OFFLINE=1.
    `cache_size` > 0 keeps that many answers per replica (SERVICE_CACHE_SIZE); `sync_seconds` > 0 makes the replica look
    that often for weights another replica downloaded (SERVICE_SYNC_SECONDS). `max_inflight` is how many /v1/ask calls may
    wait for the models at once (SERVICE_MAX_INFLIGHT, 0 = 4). `config` (the admin page's settings) replaces `warm`,
    `check_hours`, `cache_size`, `sync_seconds` and `max_inflight` and keeps them live; without it they stay fixed."""
    tokens = tokens or TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    offline = os.environ.get("SERVICE_OFFLINE") == "1"
    registry = registry or Registry(offline=offline, config=config)
    updates = None if offline else updates or Updates(registry, marker=default_marker())
    cache = AnswerCache()
    inflight = 0

    def inflight_limit() -> int:
        """Calls this replica lets wait for the models at once; more get 429. 0 = 4."""
        value = config.get("max_inflight") if config else (
            max_inflight if max_inflight is not None else int(os.environ.get("SERVICE_MAX_INFLIGHT") or 0))
        return value or 4

    async def slot():
        nonlocal inflight
        if inflight >= inflight_limit():
            raise HTTPException(429, "Laya is busy: too many requests waiting", headers={"Retry-After": "5"})
        inflight += 1
        try:
            yield
        finally:
            inflight -= 1

    def cache_key(body: AskRequest) -> str:
        part = [body.model_dump(exclude={"stream"}), registry.cache_part(body.project)]
        return hashlib.sha256(json.dumps(part, sort_keys=True, default=str).encode()).hexdigest()

    def live(key: str, fixed: Any) -> Any:
        return config.get(key) if config else fixed

    async def every(seconds: Callable[[], float], job: Callable[[], Any], name: str) -> None:
        """Runs `job` in a thread each time `seconds()` have passed (0 = off). The value is read again every second,
        so a change on the admin page applies at once."""
        last = None
        while True:
            wait = seconds()
            now = time.monotonic()
            if wait > 0 and (last is None or now - last >= wait):
                last = now
                try:
                    await anyio.to_thread.run_sync(job)
                except Exception:
                    _log.exception("%s failed", name)
            await anyio.sleep(min(wait, 1.0) if wait > 0 else 1.0)

    def threads_job() -> None:
        """Applies a CPU thread count saved from another replica's admin page."""
        saved = settings.load(tokens.settings_path).get("torch_threads")
        current = _torch_threads()
        if saved and current is not None and saved != current:
            _set_torch_threads(int(saved))

    def sync_job() -> None:
        if updates.sync():
            _log.info("Reloaded models: new weights were downloaded by another replica")

    async def warm_up() -> None:
        """In the background, so /health answers while the first start downloads the models."""
        try:
            await anyio.to_thread.run_sync(registry.warm, abandon_on_cancel=True)
        except Exception:
            _log.exception("Model warm-up failed; the first request will try again")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with anyio.create_task_group() as background:
            if live("warm", warm):
                background.start_soon(warm_up)
            if updates:
                background.start_soon(every, lambda: live("update_check_hours", check_hours) * 3600, updates.check, "Update check")
                background.start_soon(every, lambda: live("sync_seconds", sync_seconds), sync_job, "Weight sync")
            if config:
                background.start_soon(every, lambda: 1.0, threads_job, "Thread sync")
            yield
            background.cancel_scope.cancel()

    def authorize(request: Request, x_internal_token: str = Header("")) -> list[str] | None:
        """The projects the token is limited to, or None for all. Unset admin password = no auth."""
        if not admin_password:
            return None
        row = tokens.lookup(x_internal_token)
        if row is None:
            raise HTTPException(401, "Invalid token")
        wait = tokens.throttle(row)
        if wait:
            raise HTTPException(429, "Too many requests for this token", headers={"Retry-After": str(wait)})
        request.state.token_id = row["id"]
        return row.get("projects")

    def check_scope(allowed: list[str] | None, project: str | None) -> None:
        if project is not None and allowed is not None and project not in allowed:
            raise HTTPException(403, f"This token may not use project {project!r}")

    def admin(x_internal_token: str = Header("")) -> None:
        if not admin_password or not hmac.compare_digest(x_internal_token, admin_password):
            raise HTTPException(401, "Admin password required (set SERVICE_ADMIN_PASSWORD)")

    app = FastAPI(title="Laya service", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        models = registry.loaded()
        return {"status": "ok", "ready": all(models.values()), "models": models,
                "project_models": registry.loaded_projects(), "torch_threads": _torch_threads()}

    @app.get("/v1/strategies")
    def list_strategies() -> dict[str, Any]:
        return {"default": strategies.DEFAULT_STRATEGY, "strategies": list(strategies.STRATEGIES)}

    def http_failure(error: Exception) -> HTTPException:
        if isinstance(error, InputTooLong):
            return HTTPException(413, {"message": str(error), "calls": getattr(error, "calls", [])})
        if isinstance(error, ValueError):
            return HTTPException(502, {"message": str(error), "calls": getattr(error, "calls", [])})
        if isinstance(error, ProjectBusy):
            return HTTPException(503, str(error), headers={"Retry-After": "5"})
        _log.error("Laya strategy failed", exc_info=error)
        return HTTPException(500, "Laya could not answer")

    def mark(response: Response, cached: str) -> None:
        response.headers["X-Laya-Cache"] = cached  # the name callers already use
        response.headers["X-Cache"] = cached  # the name the other model services use

    async def pieces(events: Any):
        """The events of a strategy running in a worker thread, one at a time."""
        while (event := await anyio.to_thread.run_sync(next, events, None)) is not None:
            yield event

    def line(event: dict[str, Any]) -> str:
        return json.dumps(event, ensure_ascii=False) + "\n"

    def ndjson(lines: Any, cached: str) -> StreamingResponse:
        return StreamingResponse(lines, media_type="application/x-ndjson", headers={"X-Laya-Cache": cached, "X-Cache": cached})

    async def replay(result: dict[str, Any]):
        """A kept answer in the shape of a stream: each answer, then `done`."""
        for qid, answer in result["answers"].items():
            yield line({"type": "answer", "id": qid, "answer": answer})
        yield line({"type": "done", **result})

    async def live_lines(events: Any, first: tuple[str, Any] | None, key: str | None, size: int):
        """One JSON line per question as it is settled, then `done` with the same body a call without `stream` returns.
        A failure after the first answer can only be sent as an `error` line."""
        result: dict[str, Any] | None = None
        try:
            async def all_events():
                if first is not None:
                    yield first
                async for event in events:
                    yield event

            async for kind, value in all_events():
                if kind == "answer":
                    yield line({"type": "answer", "id": value[0], "answer": value[1]})
                else:
                    result = value
        except Exception as error:
            failure = http_failure(error)
            yield line({"type": "error", "status": failure.status_code, "detail": failure.detail})
            return
        if key and result is not None:
            cache.put(key, result, size)
        yield line({"type": "done", **(result or {})})

    @app.post("/v1/ask")
    async def ask(body: AskRequest, response: Response, allowed: list[str] | None = Depends(authorize),
                  _slot: None = Depends(slot)) -> Any:
        check_scope(allowed, body.project)
        try:
            check_questions(body.questions)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        tuning = strategies.Tuning(body.min_confidence, body.thresholds, body.prune_ratio, body.pair_ratio)

        size = live("cache_size", cache_size)
        key = cache_key(body) if size else None
        kept = cache.get(key, size) if key else None  # size 0 empties the cache
        if kept is not None:
            if body.stream:
                return ndjson(replay(kept), "hit")
            mark(response, "hit")
            return kept
        args = (registry, body.strategy, body.text, body.questions, body.max_len, tuning, body.trace, body.project)
        try:
            if body.stream:
                events = pieces(strategies.run_iter(*args))
                first = await anext(events, None)  # the first question is settled here, so a refusal is still an HTTP error
                return ndjson(live_lines(events, first, key, size), "miss")
            result = await anyio.to_thread.run_sync(strategies.run, *args)
        except Exception as error:
            raise http_failure(error) from None
        if key:
            cache.put(key, result, size)
            mark(response, "miss")
        return result

    @app.post("/v1/validate", dependencies=[Depends(authorize)])
    def validate(body: ValidateRequest) -> dict[str, bool]:
        """The one definition of a valid question set; callers check theirs here instead of keeping a copy."""
        try:
            check_questions(body.questions)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"ok": True}

    @app.post("/v1/combine", dependencies=[Depends(authorize)])
    def combine(body: CombineRequest) -> Any:
        try:
            return strategies.combine(body.voters, body.options, body.prune_ratio, body.pair_ratio)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HTTPException(422, "voters must be answers to one choice question over the same options") from None

    @app.put("/admin/threads", dependencies=[Depends(admin)])
    def set_threads(body: Threads) -> dict[str, Any]:
        """Applies to this replica now and is saved; the other replicas apply it within a few seconds. It beats SERVICE_TORCH_THREADS."""
        _set_torch_threads(body.torch_threads)
        settings.save(tokens.settings_path, torch_threads=body.torch_threads)
        return {"torch_threads": _torch_threads()}

    @app.get("/admin/workers", dependencies=[Depends(admin)])
    def workers() -> dict[str, Any]:
        """Read-only: replicas are a Docker Compose setting (SERVICE_REPLICAS), which the service cannot change itself."""
        replicas = os.environ.get("SERVICE_REPLICAS", "").strip()
        return {"replicas": int(replicas) if replicas.isdigit() else None}

    def enabled() -> Updates:
        if updates is None:
            raise HTTPException(409, "Updates are off while SERVICE_OFFLINE=1")
        return updates

    @app.get("/admin/updates", dependencies=[Depends(admin)])
    def update_status() -> dict[str, Any]:
        """The last check's result (no network); `enabled` is false with SERVICE_OFFLINE=1."""
        return {"enabled": updates is not None, **(updates.status() if updates else {})}

    @app.post("/admin/updates/check", dependencies=[Depends(admin)])
    async def check_updates() -> dict[str, Any]:
        return {"enabled": True, **await anyio.to_thread.run_sync(enabled().check)}

    @app.post("/admin/updates/weights", dependencies=[Depends(admin)])
    async def update_weights() -> dict[str, Any]:
        """Downloads newer weights into the shared models volume and reloads this replica; other replicas load them
        when they restart. The laya package itself cannot be changed here: rebuild the image for that."""
        try:
            return {"enabled": True, **await anyio.to_thread.run_sync(enabled().update_weights)}
        except HTTPException:
            raise
        except Exception as error:
            _log.exception("Weight update failed")
            raise HTTPException(502, f"Weight update failed: {error}") from None

    @app.get("/admin/tokens", dependencies=[Depends(admin)])
    def list_tokens() -> dict[str, Any]:
        return {"tokens": tokens.list()}

    @app.post("/admin/tokens", dependencies=[Depends(admin)])
    def create_token(body: NewToken) -> dict[str, Any]:
        return tokens.create(body.name.strip(), body.projects, body.rate, body.expires_days)

    @app.get("/admin/settings", dependencies=[Depends(admin)])
    def get_settings() -> dict[str, Any]:
        return {"settings": config.describe() if config else []}

    @app.put("/admin/settings", dependencies=[Depends(admin)])
    def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        """Saves options; null resets one. Every replica reads the shared file, so a change applies everywhere within
        a second, except options marked `restart`."""
        if config is None:
            raise HTTPException(409, "Settings are fixed in this setup")
        try:
            config.set(body.values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"settings": config.describe()}

    @app.put("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def update_token(token_id: str, body: TokenScope) -> dict[str, bool]:
        if not tokens.update(token_id, body.projects, body.rate):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    latency = LatencyHistogram()

    @app.middleware("http")
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            latency.observe(time.perf_counter() - started)
        token_id = getattr(request.state, "token_id", None)
        if token_id:
            await run_in_threadpool(tokens.record, token_id, (time.perf_counter() - started) * 1000, response.status_code >= 500)
        return response

    @app.get("/metrics", dependencies=[Depends(admin)])
    def metrics() -> PlainTextResponse:
        """Prometheus text per token, from the shared token file (up to a minute behind). Send the admin password."""
        return PlainTextResponse(tokens.metrics("laya") + latency.render("laya"))

    @app.delete("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def revoke_token(token_id: str) -> dict[str, bool]:
        if not tokens.revoke(token_id):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    web = Path(os.environ.get("SERVICE_WEB_DIR", Path(__file__).resolve().parents[1] / "web"))
    if web.is_dir():
        app.mount("/admin", StaticFiles(directory=web, html=True))

    return app


def main() -> None:
    import sys

    import uvicorn

    if "--dotenv" in sys.argv[1:]:  # run.bat asks for it; a plain `python src/app.py` and Docker never read .env
        env_file = Path(__file__).resolve().parents[1] / ".env"
        if env_file.is_file():
            names = settings.load_env_file(env_file)
            print(f".env: loaded {', '.join(names) or 'no variables'} (names only; a real environment variable wins)", flush=True)
        else:
            print(".env: no file in the service folder", flush=True)

    store = TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    config = settings.Config(store.settings_path, SETTINGS)
    saved = settings.load(store.settings_path)
    threads = saved.get("torch_threads") or os.environ.get("SERVICE_TORCH_THREADS", "").strip()
    if threads:  # before the models load
        _set_torch_threads(int(threads))
    app = create_app(admin_password=os.environ.get("SERVICE_ADMIN_PASSWORD") or None, tokens=store, config=config)
    uvicorn.run(app, host=os.environ.get("SERVICE_HOST", "127.0.0.1"), port=int(os.environ.get("SERVICE_PORT", "29220")))


if __name__ == "__main__":
    main()
