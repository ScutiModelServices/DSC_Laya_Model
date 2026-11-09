# Changelog

## Unreleased

- Admin page: a closeable warning on the Overview appears while no weights are downloaded, with a button to the Updates page. While **Download new weights** runs, the Updates card shows a progress bar with megabytes downloaded (`progress` in `GET /admin/updates`; new shared `src/progress.py`). Replicas share the numbers through a small file next to the weights marker; where that folder is not shared (Ollama services) the bar shows on the replica doing the download.

- Admin UI: separate hash-routed pages in a collapsible sidebar; health-first Overview, one main-header page title, capability-specific navigation, draft-preserving navigation and responsive desktop/mobile layout. Existing admin and public API actions are preserved.

- Admin page: the CPU threads card and tile are hidden when `CONFIG.tags` has `host-threads`, and the Project models card is shown only with `project-models`.

- Admin page: `CONFIG.tags` (same tags as `docs/purpose.md`) switch tag-driven notes: `gpu` shows a CPU-threads note, `license-restricted` shows a licence note on the Updates card.

- Breaking: startup, warm-up and inference load downloaded weights only. Before first inference open `/admin/` → Updates → Download; an empty installation keeps health/admin available and returns 503 for inference.
- Removed `SERVICE_OFFLINE`; loading downloaded weights is now the only mode, and online update checks stay available.

- `SERVICE_MODELS_DIR` sets where a local (no Docker) run keeps downloaded model weights: `settings.apply_models_dir()` maps it to `HF_HOME` and `OLLAMA_MODELS` at start.
- Docs: `docs/guide-cli.md`, a PowerShell-only way to test the service.

### Breaking: common service environment variables

Service-owned environment variables now use `SERVICE_*`. Old names are no longer read.
Rename old names once in your real `.env`, Compose `environment:` block, and CI/secret settings.

| Old name | New name |
|---|---|
| `LAYA_ADMIN_PASSWORD` | `SERVICE_ADMIN_PASSWORD` |
| `LAYA_CACHE_SIZE` | `SERVICE_CACHE_SIZE` |
| `LAYA_HOST` | `SERVICE_HOST` |
| `LAYA_MAX_INFLIGHT` | `SERVICE_MAX_INFLIGHT` |
| `LAYA_MAX_PROJECT_MODELS` | `SERVICE_MAX_PROJECT_MODELS` |
| `LAYA_MEM_LIMIT` | `SERVICE_MEM_LIMIT` |
| `LAYA_OFFLINE` | `SERVICE_OFFLINE` |
| `LAYA_PORT` | `SERVICE_PORT` |
| `LAYA_PROJECT_MODELS_DIR` | `SERVICE_PROJECT_MODELS_DIR` |
| `LAYA_PROJECT_WAIT_SECONDS` | `SERVICE_PROJECT_WAIT_SECONDS` |
| `LAYA_REPLICAS` | `SERVICE_REPLICAS` |
| `LAYA_REVISION` | `SERVICE_REVISION` |
| `LAYA_SYNC_SECONDS` | `SERVICE_SYNC_SECONDS` |
| `LAYA_TOKENS_FILE` | `SERVICE_TOKENS_FILE` |
| `LAYA_TORCH_THREADS` | `SERVICE_TORCH_THREADS` |
| `LAYA_UPDATE_CHECK_HOURS` | `SERVICE_UPDATE_CHECK_HOURS` |
| `LAYA_WARM` | `SERVICE_WARM` |
| `LAYA_WEB_DIR` | `SERVICE_WEB_DIR` |
| `LAYA_WEIGHTS_MARKER` | `SERVICE_WEIGHTS_MARKER` |

- Default HTTP port moves from 9120 to 29220. Update explicit old ports in your real `.env`, saved tool-service `backends.json` connections, and browser/MCP client configurations once. Compose now uses 29220 inside API replicas and nginx; deployments using the old Compose default must recreate containers. A custom host-port override stays in effect until you change it; container ports still move to 29220.

- Renamed `LayaModel` to `DSC_Laya_Model`; Docker project, API image, services, load balancer and volumes now use `dsc_laya_model` names. Callers on `citadel_core` connect to `dsc_laya_model_lb` at the existing port.
- Docker volume migration: existing volumes with old names are orphaned; renaming does not move data. Stop the old stack before copying; never use `down -v`. The commands below assume the previous default Compose project name; substitute actual volume names if `-p` or `COMPOSE_PROJECT_NAME` overrode it.
  - `laya_models` → `dsc_laya_model_models`: `docker volume inspect laya_models && docker volume create dsc_laya_model_models && docker run --rm -v laya_models:/from:ro -v dsc_laya_model_models:/to alpine:3.21 sh -c "cd /from && cp -a . /to/"`
  - `laya_tokens` → `dsc_laya_model_data`: `docker volume inspect laya_tokens && docker volume create dsc_laya_model_data && docker run --rm -v laya_tokens:/from:ro -v dsc_laya_model_data:/to alpine:3.21 sh -c "cd /from && cp -a . /to/"`
- To retain an old volume instead, set its top-level volume definition to `external: true` and `name: <old-volume-name>` in a local Compose override. Rollback: stop the renamed stack, restore the previous checkout and folder name, and restart the old stack with its original volumes; copy back newer data first if the renamed stack accepted writes.

- `run.bat` loads `.env` from the service folder for a local run: it passes `--dotenv`, the service prints which variable names it loaded (never the values), and a real environment variable still wins. A plain `python src/app.py` and Docker never read `.env`.
- Untracked folders now start with `.` or `_`: `zip_versions/` is `_zip_versions/`. A local `voices`, `tem_code` or `zip_versions` folder from an older checkout needs renaming by hand.
- `run.bat` is now a menu (run the service, deploy, build the release ZIP) and `run.bat run|deploy|zip` skips it. `tools/deploy.bat`, `tools/make_zip.bat` and `tools/deploy.local.example.bat` are gone; the local deploy target is `tools/deploy.local.ini` (copy `tools/deploy.local.example.ini`).
- Admin page: **Connect to this service** card with the addresses other services can use (this page's address, same PC, Docker network, `host.docker.internal`) and every endpoint, each with a Copy button.
- Admin **Try it** panel: descriptor-driven public API requests, live text streams, JSON/media output, status and latency, cancellation and redacted curl. Auth choice (b): pasted public token stays in memory; normal scope, limits and caches apply. Shared `web/playground.js` is copied across all model and tool services.

- `run.bat`: runs `uv sync` directly; `python -m uv` failed when uv was installed only as a program (winget).
- Start-up: the models load in the background, so `/health` answers while the first start downloads them (like the other model services); a failed warm-up is logged and the first request tries again. The compose health check now allows 300 s to start.
- Docker: `.dockerignore` keeps `.git`, `.venv`, tests and local state out of the build context (the same file in every model service).
- Settings: `max_inflight` (`SERVICE_MAX_INFLIGHT`, 0 = 4): more `/v1/ask` calls waiting for the models than that get `429` with `Retry-After: 5`, like the other model services.
- Deploy: `README.md` is published to `live` like in the other model services.
- Compose: `stop_grace_period: 90s`, so a restart lets a running answer finish instead of cutting it off after Docker's default 10 s.
- Docs: DSC_Laya_Model is its own stack; Citadel calls it over HTTP and does not build it, so the "must sit next to Citadel" notes in `deployment.md` and `tools/deploy.bat` are gone.
- Docs: set CPU threads to the number of physical cores, not logical CPUs.
- `/metrics` also returns `laya_request_seconds`, a histogram of how long `/v1` calls took on that replica.
- Compose: optional `SERVICE_MEM_LIMIT` caps the container's RAM (default no cap).

- `POST /v1/ask` accepts `"stream": true`: each question's answer arrives as a JSON line when it is settled, then `done`. The answer cache also sends an `X-Cache` header (next to `X-Laya-Cache`) and uses the cache class shared with the other model services.

- **Settings** card on `/admin/`: the update interval, cache and limits, project options and similar are saved in `settings.json`, beat the environment variable and apply on every replica within seconds (`GET` and `PUT /admin/settings`). The compose file no longer passes these; the admin password, ports, replica count and Docker settings stay in `.env`.

- Tokens: edit the project list and a calls-per-minute limit without revoking (`PUT /admin/tokens/{id}`), optional expiry, per-token errors and average time on the admin page, and `GET /metrics` (Prometheus text). Over the rate limit a call gets `429` with `Retry-After`.
- Admin page: **Project models** card.
- `/v1/ask` answer cache (`SERVICE_CACHE_SIZE`, default 256 per replica, header `X-Laya-Cache`); new weights and replaced project files clear it.
- Replicas reload by themselves after another replica downloaded new weights (`SERVICE_SYNC_SECONDS`, default 10).

- Tokens can be limited to a list of projects (set on the admin page when creating one). Another project is `403`; no `project` is always allowed.

## 1.1.0

- Optional validated `project` on `/v1/ask` serves local typed-decisions checkpoints; English stays base.
  `SERVICE_PROJECT_MODELS_DIR` enables local artifacts and `SERVICE_MAX_PROJECT_MODELS` (default 2) bounds resident
  project engines with LRU eviction of idle engines. Base engines are never evicted. Missing artifacts fall back.
- Responses and usage-log calls report actual `model` and `fallback`; `project_used` is false when the strategy never
  asked the typed model. When all project slots stay busy past `SERVICE_PROJECT_WAIT_SECONDS` (30) the answer is `503`.
  `/health` lists loaded `project_models`.
  Hub weight updates leave project files and resident project engines alone.

- Admin page **Updates** card: reports a newer `laya` package (PyPI) and newer weights (Hugging Face), checks every
  `SERVICE_UPDATE_CHECK_HOURS` (default 6), and downloads new weights on request, then reloads the models on that replica.
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
