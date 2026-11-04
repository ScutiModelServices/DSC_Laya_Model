# Changelog

## Unreleased

- Deploy: `README.md` is published to `live` like in the other model services.
- Compose: `stop_grace_period: 90s`, so a restart lets a running answer finish instead of cutting it off after Docker's default 10 s.
- Docs: set CPU threads to the number of physical cores, not logical CPUs.
- `/metrics` also returns `laya_request_seconds`, a histogram of how long `/v1` calls took on that replica.
- Compose: optional `LAYA_MEM_LIMIT` caps the container's RAM (default no cap).

- `POST /v1/ask` accepts `"stream": true`: each question's answer arrives as a JSON line when it is settled, then `done`. The answer cache also sends an `X-Cache` header (next to `X-Laya-Cache`) and uses the cache class shared with the other model services.

- **Settings** card on `/admin/`: the update interval, cache and limits, project options and similar are saved in `settings.json`, beat the environment variable and apply on every replica within seconds (`GET` and `PUT /admin/settings`). The compose file no longer passes these; the admin password, ports, replica count and Docker settings stay in `.env`.

- Tokens: edit the project list and a calls-per-minute limit without revoking (`PUT /admin/tokens/{id}`), optional expiry, per-token errors and average time on the admin page, and `GET /metrics` (Prometheus text). Over the rate limit a call gets `429` with `Retry-After`.
- Admin page: **Project models** card.
- `/v1/ask` answer cache (`LAYA_CACHE_SIZE`, default 256 per replica, header `X-Laya-Cache`); new weights and replaced project files clear it.
- Replicas reload by themselves after another replica downloaded new weights (`LAYA_SYNC_SECONDS`, default 10).

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
