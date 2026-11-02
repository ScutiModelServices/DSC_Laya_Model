"""Looks for newer Laya releases: the `laya` package on PyPI and the weights on the Hugging Face Hub.

Checking only reads. `update_weights` downloads new weights into the shared models volume and makes this replica
load them. The package cannot change inside a running container, so a newer one is only reported.

shortcut: versions are compared for inequality, not order; upgrade if a downgrade must not count as an update."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from importlib import metadata
from typing import Any, Callable

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

    snapshot_download(repo_id=repo, token=False)


class Updates:
    """The `latest_package`, `remote_sha`, `cached_sha`, `download` and `installed` callables are injectable for tests."""

    def __init__(self, registry: Any, *, latest_package: Callable[[], str] = _latest_package,
                 remote_sha: Callable[[str], str] = _remote_sha, cached_sha: Callable[[str], str | None] = _cached_sha,
                 download: Callable[[str], None] = _download,
                 installed: Callable[[], str] = lambda: metadata.version(PACKAGE),
                 model_repos: Callable[[], list[str]] | None = None) -> None:
        self._registry = registry
        self._latest_package, self._remote_sha, self._cached_sha = latest_package, remote_sha, cached_sha
        self._download, self._installed = download, installed
        self._repos = model_repos or (lambda: repos(registry.models))
        self._lock = threading.Lock()  # one check or download at a time
        self._status: dict[str, Any] = {"checked_at": None, "error": None, "package": None, "weights": []}

    def status(self) -> dict[str, Any]:
        """The result of the last check; never touches the network."""
        return self._status

    def check(self) -> dict[str, Any]:
        with self._lock:
            return self._check()

    def _check(self) -> dict[str, Any]:
        status: dict[str, Any] = {"checked_at": time.time(), "error": None, "package": None, "weights": []}
        try:
            installed, latest = self._installed(), self._latest_package()
            status["package"] = {"name": PACKAGE, "installed": installed, "latest": latest, "update": installed != latest}
            for repo in self._repos():
                cached, latest = self._cached_sha(repo), self._remote_sha(repo)
                status["weights"].append({"repo": repo, "cached": cached, "latest": latest, "update": cached != latest})
        except Exception as error:  # offline, rate limited, repo gone: shown on the admin page, not raised
            status["error"] = f"{type(error).__name__}: {error}"
        self._status = status
        return status

    def update_weights(self) -> dict[str, Any]:
        """Download every repository that has a newer revision, then reload the models. Raises when a download fails."""
        with self._lock:
            status = self._check()
            if status["error"]:
                raise RuntimeError(status["error"])
            stale = [item["repo"] for item in status["weights"] if item["update"]]
            for repo in stale:
                self._download(repo)
            if stale:
                self._registry.reload()
            return self._check()
