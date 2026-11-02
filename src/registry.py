"""Lazy, one-per-checkpoint Laya engines. Inference is not thread safe, so each engine has its own lock."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

# The checkpoints callers may ask for. Anything else is refused before a download can start.
MODELS = ("convaiinnovations/laya", "typed-decisions")

_REQUIRED_FILES = ("rl_agent_config.json", "model.safetensors", "encoder/config.json", "tokenizer/tokenizer.json")


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

    return laya.load(cached_checkpoint(checkpoint) if offline else checkpoint)


class Registry:
    """`loader(checkpoint)` builds an engine with `predict(text, questions, max_len=)`; injectable for tests."""

    def __init__(self, loader: Callable[[str], Any] | None = None, offline: bool = False) -> None:
        self._loader = loader or (lambda checkpoint: _load_laya(checkpoint, offline))
        self._engines: dict[str, Any] = {}
        self._locks = {model: threading.Lock() for model in MODELS}

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
        with self._locks[model]:
            engine = self._engines.get(model)
            if engine is None:
                engine = self._engines[model] = self._loader(model)
            if call is None:
                return None
            text, questions, max_len = call
            return engine.predict(text, questions, max_len=max_len)
