"""HTTP face of the Laya models.

POST /v1/ask {"strategy", "text", "questions", ...} answers typed questions with the chosen decision
strategy (see strategies.py); callers pick a strategy, never a model.
POST /v1/validate checks a question set against the one set of rules.
POST /v1/combine mixes a caller's own voter (Garrison's cloud fallback) into the local models' answers.
GET /v1/strategies lists the strategies; GET /health reports which models are loaded."""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import anyio.to_thread
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import strategies
from answers import InputTooLong, check_questions
from registry import PROJECT_PATTERN, ProjectBusy, Registry
import settings
from tokens import TokenStore
from updates import Updates

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


class ValidateRequest(BaseModel):
    questions: Any


class CombineRequest(BaseModel):
    voters: dict[str, dict[str, Any]] = Field(min_length=1, max_length=4)
    options: list[str] | None = Field(None, min_length=2, max_length=10)
    prune_ratio: float = Field(strategies.PRUNE_RATIO, ge=0, lt=1)
    pair_ratio: float = Field(strategies.PAIR_RATIO, gt=0, le=1)


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    projects: list[Annotated[str, Field(pattern=PROJECT_PATTERN)]] | None = Field(None, max_length=32)


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
    prediction several times slower. LAYA_TORCH_THREADS sets this at start; /admin/threads changes it while running."""
    import torch

    torch.set_num_threads(threads)


def create_app(registry: Registry | None = None, admin_password: str | None = None, warm: bool = False,
               tokens: TokenStore | None = None, updates: Updates | None = None, check_hours: float = 0) -> FastAPI:
    """`admin_password` (LAYA_ADMIN_PASSWORD) unlocks /admin/, where each project gets a token of its own; when it
    is set, every call except /health must send one of those tokens in the X-Internal-Token header. Unset = no
    auth at all (local use only). `tokens` holds the project tokens (LAYA_TOKENS_FILE). `check_hours` > 0 looks for
    newer Laya releases that often (LAYA_UPDATE_CHECK_HOURS); it never downloads. `updates` is off with LAYA_OFFLINE=1."""
    tokens = tokens or TokenStore(os.environ.get("LAYA_TOKENS_FILE", "tokens.json"))
    offline = os.environ.get("LAYA_OFFLINE") == "1"
    registry = registry or Registry(offline=offline)
    updates = None if offline else updates or Updates(registry)

    async def check_forever() -> None:
        while True:
            try:
                await anyio.to_thread.run_sync(updates.check)
            except Exception:
                _log.exception("Update check failed")
            await anyio.sleep(check_hours * 3600)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if warm:
            await anyio.to_thread.run_sync(registry.warm)
        async with anyio.create_task_group() as background:
            if updates and check_hours > 0:
                background.start_soon(check_forever)
            yield
            background.cancel_scope.cancel()

    def authorize(x_internal_token: str = Header("")) -> list[str] | None:
        """The projects the token is limited to, or None for all. Unset admin password = no auth."""
        if not admin_password:
            return None
        row = tokens.lookup(x_internal_token)
        if row is None:
            raise HTTPException(401, "Invalid token")
        return row.get("projects")

    def check_scope(allowed: list[str] | None, project: str | None) -> None:
        if project is not None and allowed is not None and project not in allowed:
            raise HTTPException(403, f"This token may not use project {project!r}")

    def admin(x_internal_token: str = Header("")) -> None:
        if not admin_password or not hmac.compare_digest(x_internal_token, admin_password):
            raise HTTPException(401, "Admin password required (set LAYA_ADMIN_PASSWORD)")

    app = FastAPI(title="Laya service", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        models = registry.loaded()
        return {"status": "ok", "ready": all(models.values()), "models": models,
                "project_models": registry.loaded_projects(), "torch_threads": _torch_threads()}

    @app.get("/v1/strategies")
    def list_strategies() -> dict[str, Any]:
        return {"default": strategies.DEFAULT_STRATEGY, "strategies": list(strategies.STRATEGIES)}

    @app.post("/v1/ask")
    async def ask(body: AskRequest, allowed: list[str] | None = Depends(authorize)) -> Any:
        check_scope(allowed, body.project)
        try:
            check_questions(body.questions)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        tuning = strategies.Tuning(body.min_confidence, body.thresholds, body.prune_ratio, body.pair_ratio)

        def failure(status: int, error: ValueError) -> HTTPException:
            return HTTPException(status, {"message": str(error), "calls": getattr(error, "calls", [])})

        try:
            return await anyio.to_thread.run_sync(
                strategies.run, registry, body.strategy, body.text, body.questions, body.max_len, tuning, body.trace, body.project,
            )
        except InputTooLong as error:
            raise failure(413, error) from None
        except ValueError as error:
            raise failure(502, error) from None
        except ProjectBusy as error:
            raise HTTPException(503, str(error), headers={"Retry-After": "5"}) from None
        except Exception:
            _log.exception("Laya strategy failed")
            raise HTTPException(500, "Laya could not answer") from None

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
        """Applies to this replica now and is saved; other replicas pick it up when they restart. It beats LAYA_TORCH_THREADS."""
        _set_torch_threads(body.torch_threads)
        settings.save(tokens.settings_path, torch_threads=body.torch_threads)
        return {"torch_threads": _torch_threads()}

    @app.get("/admin/workers", dependencies=[Depends(admin)])
    def workers() -> dict[str, Any]:
        """Read-only: replicas are a Docker Compose setting (LAYA_REPLICAS), which the service cannot change itself."""
        replicas = os.environ.get("LAYA_REPLICAS", "").strip()
        return {"replicas": int(replicas) if replicas.isdigit() else None}

    def enabled() -> Updates:
        if updates is None:
            raise HTTPException(409, "Updates are off while LAYA_OFFLINE=1")
        return updates

    @app.get("/admin/updates", dependencies=[Depends(admin)])
    def update_status() -> dict[str, Any]:
        """The last check's result (no network); `enabled` is false with LAYA_OFFLINE=1."""
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
        return tokens.create(body.name.strip(), body.projects)

    @app.delete("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def revoke_token(token_id: str) -> dict[str, bool]:
        if not tokens.revoke(token_id):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    web = Path(os.environ.get("LAYA_WEB_DIR", Path(__file__).resolve().parents[1] / "web"))
    if web.is_dir():
        app.mount("/admin", StaticFiles(directory=web, html=True))

    return app


def main() -> None:
    import uvicorn

    saved = settings.load(TokenStore(os.environ.get("LAYA_TOKENS_FILE", "tokens.json")).settings_path)
    threads = saved.get("torch_threads") or os.environ.get("LAYA_TORCH_THREADS", "").strip()
    if threads:  # before the models load
        _set_torch_threads(int(threads))
    app = create_app(admin_password=os.environ.get("LAYA_ADMIN_PASSWORD") or None, warm=os.environ.get("LAYA_WARM", "1") == "1",
                     check_hours=float(os.environ.get("LAYA_UPDATE_CHECK_HOURS", "6") or 0))
    uvicorn.run(app, host=os.environ.get("LAYA_HOST", "127.0.0.1"), port=int(os.environ.get("LAYA_PORT", "9120")))


if __name__ == "__main__":
    main()
