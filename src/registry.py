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

# The checkpoints callers may ask for. Loading always uses downloaded local files.
MODELS = ("convaiinnovations/laya", "typed-decisions")

_REQUIRED_FILES = ("rl_agent_config.json", "model.safetensors", "encoder/config.json", "tokenizer/tokenizer.json")
PROJECT_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,47}$"


def cached_checkpoint(checkpoint: str) -> str:
    """Absolute path of a complete, already downloaded checkpoint; never touches the network."""
    from huggingface_hub import snapshot_download
    from laya.router import resolve_model_spec

    repo, subfolder = resolve_model_spec(checkpoint) or (checkpoint, None)
    try:
        root = Path(snapshot_download(repo_id=repo, local_files_only=True, token=False))
        root = (root / subfolder if subfolder else root).resolve()
    except (OSError, ValueError):
        raise WeightsMissing() from None
    if not all((root / name).is_file() for name in _REQUIRED_FILES):
        raise WeightsMissing()
    return str(root)


class WeightsMissing(RuntimeError):
    """Downloaded base weights are missing; expose only an admin recovery hint."""

    def __init__(self) -> None:
        super().__init__("Model weights are not downloaded. Ask an admin to press Download on the Updates card.")


def _load_laya(checkpoint: str) -> Any:
    import laya

    return laya.load(cached_checkpoint(checkpoint) if checkpoint in MODELS else checkpoint)


def _stamp(checkpoint: str) -> tuple:
    """Modification times of the checkpoint files; replacing a file changes it."""
    try:
        return tuple((Path(checkpoint) / name).stat().st_mtime_ns for name in _REQUIRED_FILES)
    except OSError:
        return ()


class ProjectBusy(RuntimeError):
    """Every project slot is in use and none freed within SERVICE_PROJECT_WAIT_SECONDS."""


@dataclass
class _ProjectEngine:
    """A resident engine and its request leases; leased engines cannot be evicted."""

    checkpoint: str
    engine: Any
    lock: Any = field(default_factory=threading.Lock)
    users: int = 0
    stamp: tuple = ()  # file mtimes when the engine was loaded


class Registry:
    """`loader(checkpoint)` builds an engine with `predict(text, questions, max_len=)`; injectable for tests."""

    def __init__(self, loader: Callable[[str], Any] | None = None, config: Any = None) -> None:
        self._loader = loader or _load_laya
        self._engines: dict[str, Any] = {}
        self.generation = 0  # bumped when the base weights are reloaded; part of the answer cache key
        self._locks = {model: threading.Lock() for model in MODELS}
        self._config = config  # with one, the admin page can change the project settings at any time
        self._fixed = {"project_models_dir": os.environ.get("SERVICE_PROJECT_MODELS_DIR") or "",
                       "max_project_models": int(os.environ.get("SERVICE_MAX_PROJECT_MODELS", "2")),
                       "project_wait_seconds": float(os.environ.get("SERVICE_PROJECT_WAIT_SECONDS", "30"))}
        if config is None and self._fixed["max_project_models"] < 1:
            raise ValueError("SERVICE_MAX_PROJECT_MODELS must be at least 1")
        if config is None and self._fixed["project_wait_seconds"] < 0:
            raise ValueError("SERVICE_PROJECT_WAIT_SECONDS must not be negative")
        self._projects: OrderedDict[str, _ProjectEngine] = OrderedDict()
        self._project_guard = threading.Condition()

    def _option(self, key: str) -> Any:
        return self._config.get(key) if self._config else self._fixed[key]

    @property
    def _project_root(self) -> Path | None:
        root = self._option("project_models_dir")
        return Path(root).resolve() if root else None

    @property
    def _project_cap(self) -> int:
        return self._option("max_project_models")

    @property
    def _project_wait(self) -> float:
        return self._option("project_wait_seconds")

    def loaded_projects(self) -> dict[str, str]:
        """Project names and checkpoint paths currently resident in this replica."""
        with self._project_guard:
            return {name: entry.checkpoint for name, entry in self._projects.items() if entry.engine is not None}

    def _project_checkpoint(self, project: str) -> str | None:
        if not re.fullmatch(PROJECT_PATTERN, project):
            raise ValueError("Invalid project name")
        root = self._project_root
        if root is None:
            return None
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

        When every slot is busy, wait up to SERVICE_PROJECT_WAIT_SECONDS for one (then ProjectBusy) instead of exceeding
        the cap or evicting an engine in use.
        Slots are reserved under the cache guard; loading and inference use each engine's own lock.
        """
        checkpoint = self._project_checkpoint(project) if project is not None else None
        if checkpoint is None:
            yield MODELS[1], project is not None
            return
        stamp = _stamp(checkpoint)
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
                entry = _ProjectEngine(checkpoint, None, stamp=stamp)
                self._projects[project] = entry
            elif not entry.users and entry.stamp != stamp:
                entry.engine, entry.stamp = None, stamp  # files were replaced: load the new ones on this lease
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
        self.generation += 1

    def cache_part(self, project: str | None) -> tuple:
        """Changes whenever the weights an answer to this project would come from change."""
        checkpoint = self._project_checkpoint(project) if project is not None else None
        return (self.generation, checkpoint, _stamp(checkpoint) if checkpoint else ())

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
