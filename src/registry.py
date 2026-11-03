"""Lazy, one-per-checkpoint Laya engines. Inference is not thread safe, so each engine has its own lock."""

from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# The checkpoints callers may ask for. Anything else is refused before a download can start.
MODELS = ("convaiinnovations/laya", "typed-decisions")

_REQUIRED_FILES = ("rl_agent_config.json", "model.safetensors", "encoder/config.json", "tokenizer/tokenizer.json")
PROJECT_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,47}$"


def cached_checkpoint(checkpoint: str) -> str:
    """Absolute path of a complete, already downloaded checkpoint; never touches the network."""
    from huggingface_hub import snapshot_download
    from laya.revisions import resolve_revision
    from laya.router import resolve_model_spec

    repo, subfolder = resolve_model_spec(checkpoint) or (checkpoint, None)
    try:
        root = Path(snapshot_download(repo_id=repo, revision=resolve_revision(repo, None),
                                      local_files_only=True, token=False))
        root = (root / subfolder if subfolder else root).resolve()
    except (OSError, ValueError):
        raise RuntimeError(f"Checkpoint {checkpoint!r} is not cached") from None
    if not all((root / name).is_file() for name in _REQUIRED_FILES):
        raise RuntimeError(f"Checkpoint {checkpoint!r} is not cached completely")
    return str(root)


def _load_laya(checkpoint: str, offline: bool) -> Any:
    import laya

    return laya.load(cached_checkpoint(checkpoint) if offline and checkpoint in MODELS else checkpoint)


class ProjectBusy(RuntimeError):
    """Every project slot is in use and none freed within LAYA_PROJECT_WAIT_SECONDS."""


@dataclass
class _ProjectEngine:
    """A resident engine and its request leases; leased engines cannot be evicted."""

    checkpoint: str
    engine: Any
    lock: Any = field(default_factory=threading.Lock)
    users: int = 0


class Registry:
    """`loader(checkpoint)` builds an engine with `predict(text, questions, max_len=)`; injectable for tests."""

    def __init__(self, loader: Callable[[str], Any] | None = None, offline: bool = False) -> None:
        self._loader = loader or (lambda checkpoint: _load_laya(checkpoint, offline))
        self._engines: dict[str, Any] = {}
        self._locks = {model: threading.Lock() for model in MODELS}
        root = os.environ.get("LAYA_PROJECT_MODELS_DIR")
        self._project_root = Path(root).resolve() if root else None
        self._project_cap = int(os.environ.get("LAYA_MAX_PROJECT_MODELS", "2"))
        if self._project_cap < 1:
            raise ValueError("LAYA_MAX_PROJECT_MODELS must be at least 1")
        self._project_wait = float(os.environ.get("LAYA_PROJECT_WAIT_SECONDS", "30"))
        if self._project_wait < 0:
            raise ValueError("LAYA_PROJECT_WAIT_SECONDS must not be negative")
        self._projects: OrderedDict[str, _ProjectEngine] = OrderedDict()
        self._project_guard = threading.Condition()

    def loaded_projects(self) -> dict[str, str]:
        """Project names and checkpoint paths currently resident in this replica."""
        with self._project_guard:
            return {name: entry.checkpoint for name, entry in self._projects.items() if entry.engine is not None}

    def _project_checkpoint(self, project: str) -> str | None:
        if not re.fullmatch(PROJECT_PATTERN, project):
            raise ValueError("Invalid project name")
        if self._project_root is None:
            return None
        root = self._project_root
        path = (root / project).resolve()
        # Directory aliases cannot create duplicate cache entries for one checkpoint.
        if path != root / project or not path.is_dir():
            return None
        if not all((path / name).resolve().is_relative_to(root) and (path / name).is_file()
                   for name in _REQUIRED_FILES):
            return None
        return str(path)

    @contextmanager
    def project_model(self, project: str | None):
        """Lease a local typed-decisions replacement, or the base model. Run in a worker thread.

        When every slot is busy, wait up to LAYA_PROJECT_WAIT_SECONDS for one (then ProjectBusy) instead of exceeding
        the cap or evicting an engine in use.
        Slots are reserved under the cache guard; loading and inference use each engine's own lock.
        """
        checkpoint = self._project_checkpoint(project) if project is not None else None
        if checkpoint is None:
            yield MODELS[1], project is not None
            return
        deadline = time.monotonic() + self._project_wait
        with self._project_guard:
            while project not in self._projects and len(self._projects) >= self._project_cap:
                idle = next((name for name, entry in self._projects.items() if not entry.users), None)
                if idle is None:
                    left = deadline - time.monotonic()
                    if left <= 0 or not self._project_guard.wait(left):
                        raise ProjectBusy(f"All {self._project_cap} project model slots are busy")
                else:
                    del self._projects[idle]
            entry = self._projects.get(project)
            if entry is None:
                entry = _ProjectEngine(checkpoint, None)
                self._projects[project] = entry
            entry.users += 1
            self._projects.move_to_end(project)
        try:
            yield entry.checkpoint, False
        finally:
            with self._project_guard:
                entry.users -= 1
                self._project_guard.notify_all()

    def loaded(self) -> dict[str, bool]:
        return {model: model in self._engines for model in MODELS}

    models = MODELS

    def reload(self) -> None:
        """Load again every model that is loaded, so newly downloaded weights are used; the others load on first use."""
        for model in MODELS:
            with self._locks[model]:
                if self._engines.pop(model, None) is not None:
                    self._engines[model] = self._loader(model)

    def warm(self) -> None:
        for model in MODELS:
            self.predict(model, None)

    def predict(self, model: str, call: tuple[str, dict, int] | None) -> dict[str, Any] | None:
        """Run one prediction, or only load the model when `call` is None."""
        if model not in MODELS:
            # Only paths admitted by project_model may reach the local loader.
            with self._project_guard:
                entry = next((entry for entry in self._projects.values() if entry.checkpoint == model), None)
            if entry is None:
                raise ValueError("Unknown model")
            with entry.lock:
                if entry.engine is None:
                    entry.engine = self._loader(model)
                return None if call is None else entry.engine.predict(call[0], call[1], max_len=call[2])
        with self._locks[model]:
            engine = self._engines.get(model)
            if engine is None:
                engine = self._engines[model] = self._loader(model)
            if call is None:
                return None
            text, questions, max_len = call
            return engine.predict(text, questions, max_len=max_len)
