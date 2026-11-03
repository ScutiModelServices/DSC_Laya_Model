# Changelog

## Unreleased

- Tokens can be limited to a list of projects (set on the admin page when creating one). Another project is `403`; no `project` is always allowed.

## 1.1.0

- Optional validated `project` on `/v1/ask` serves local typed-decisions checkpoints; English stays base.
  `LAYA_PROJECT_MODELS_DIR` enables local artifacts and `LAYA_MAX_PROJECT_MODELS` (default 2) bounds resident
  project engines with LRU eviction of idle engines. Base engines are never evicted. Missing artifacts fall back.
- Responses and usage-log calls report actual `model` and `fallback`; `project_used` is false when the strategy never
  asked the typed model. When all project slots stay busy past `LAYA_PROJECT_WAIT_SECONDS` (30) the answer is `503`.
  `/health` lists loaded `project_models`.
  Hub weight updates leave project files and resident project engines alone.

- Admin page **Updates** card: reports a newer `laya` package (PyPI) and newer weights (Hugging Face), checks every
  `LAYA_UPDATE_CHECK_HOURS` (default 6), and downloads new weights on request, then reloads the models on that replica.
  The package is only reported. New routes: `GET /admin/updates`, `POST /admin/updates/check`, `POST /admin/updates/weights`.

## 1.0.0

First public release.

- Admin page at `/admin/`: health badge with polling, token list with created, last used and request count,
  copy button, in-app revoke dialog, CPU thread control, light and dark themes, phone layout.
- Per-project tokens stored as hashes; `last_used` and `requests` are written at most once a minute per token,
  so the counters are approximate and a crash can lose up to a minute of them.
- Docker Compose deployment behind nginx with configurable replicas; guides for Docker on the same device,
  Docker on a deployment device, and no Docker.
- Browser smoke test (Playwright) and a GitHub Actions workflow that runs the tests.
