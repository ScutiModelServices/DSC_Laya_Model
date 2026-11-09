"""Looks for newer Laya releases: the `laya` package on PyPI and the weights on the Hugging Face Hub.

Checking only reads. `update_weights` downloads new weights into the shared models volume and makes this replica
load them. The package cannot change inside a running container, so a newer one is only reported.

shortcut: versions are compared for inequality, not order; upgrade if a downgrade must not count as an update."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

from progress import hub_blobs, hub_total, progress, tracking

PACKAGE = "laya"


def _latest_package() -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{PACKAGE}/json", timeout=15) as response:
        return json.load(response)["info"]["version"]


def repos(models: tuple[str, ...]) -> list[str]:
    """The Hub repositories behind the model names (several names can share one repository)."""
    from laya.router import resolve_model_spec

    found: list[str] = []
    for model in models:
        repo = (resolve_model_spec(model) or (model, None))[0]
        if repo not in found:
            found.append(repo)
    return found


def _remote_sha(repo: str) -> str:
    from huggingface_hub import HfApi

    return HfApi().model_info(repo, token=False).sha


def _cached_sha(repo: str) -> str | None:
    from huggingface_hub import snapshot_download
    from laya.revisions import snapshot_revision

    try:
        return snapshot_revision(snapshot_download(repo_id=repo, local_files_only=True, token=False))
    except (OSError, ValueError):
        return None


def _download(repo: str) -> None:
    from huggingface_hub import snapshot_download

    with tracking(repo, hub_total(repo, None), hub_blobs(repo)):
        snapshot_download(repo_id=repo, token=False)


def default_marker() -> Path:
    """File in the shared models volume whose content changes when one replica downloads new weights."""
    configured = os.environ.get("SERVICE_WEIGHTS_MARKER")
    return Path(configured) if configured else Path(os.environ.get("HF_HOME") or "~/.cache/huggingface").expanduser() / ".laya-weights-version"


class Updates:
    """The `latest_package`, `remote_sha`, `cached_sha`, `download` and `installed` callables are injectable for tests."""

    def __init__(self, registry: Any, *, latest_package: Callable[[], str] = _latest_package,
                 remote_sha: Callable[[str], str] = _remote_sha, cached_sha: Callable[[str], str | None] = _cached_sha,
                 download: Callable[[str], None] = _download,
                 installed: Callable[[], str] = lambda: metadata.version(PACKAGE),
                 model_repos: Callable[[], list[str]] | None = None, marker: Path | None = None) -> None:
        self._registry = registry
        self._marker = marker
        self._seen = self._read_marker()
        self._latest_package, self._remote_sha, self._cached_sha = latest_package, remote_sha, cached_sha
        self._download, self._installed = download, installed
        self._repos = model_repos or (lambda: repos(registry.models))
        self._lock = threading.Lock()  # one check or download at a time
        self._status: dict[str, Any] = {"checked_at": None, "error": None, "package": None, "weights": []}

    def status(self) -> dict[str, Any]:
        """The result of the last check; never touches the network."""
        return {**self._status, "progress": progress.snapshot()}

    def check(self) -> dict[str, Any]:
        with self._lock:
            return self._check()

    def _check(self) -> dict[str, Any]:
        status: dict[str, Any] = {"checked_at": time.time(), "error": None, "package": None, "weights": []}
        try:
            installed, latest = self._installed(), self._latest_package()
            status["package"] = {"name": PACKAGE, "installed": installed, "latest": latest, "update": installed != latest}
        except Exception as error:  # offline, rate limited, repo gone: shown on the admin page, not raised
            status["error"] = f"{type(error).__name__}: {error}"
        for repo in self._repos():
            cached, latest = self._cached_sha(repo), None
            try:
                latest = self._remote_sha(repo)
            except Exception as error:
                status["error"] = f"{type(error).__name__}: {error}"
            status["weights"].append({"repo": repo, "cached": cached, "latest": latest,
                                      "update": latest is not None and cached != latest})
        self._status = status
        return status

    def _read_marker(self) -> str:
        try:
            return self._marker.read_text("utf-8") if self._marker else ""
        except OSError:
            return ""

    def sync(self) -> bool:
        """Reload the models when another replica downloaded new weights since the last call."""
        current = self._read_marker()
        if current == self._seen:
            return False
        self._seen = current
        self._registry.reload()
        return True

    def _announce(self) -> None:
        if self._marker:
            self._marker.parent.mkdir(parents=True, exist_ok=True)
            self._seen = uuid.uuid4().hex
            self._marker.write_text(self._seen, "utf-8")

    def update_weights(self) -> dict[str, Any]:
        """An explicit admin action replaces every downloaded repository, then reloads replicas."""
        with self._lock:
            stale = self._repos()
            for repo in stale:
                self._download(repo)
            if stale:
                self._registry.reload()
                self._announce()
            result = {**self._check()}
            if stale and self._marker:
                result["reload_note"] = "Weights downloaded; this replica loaded them. The other replicas reload them within a few seconds."
            return result
