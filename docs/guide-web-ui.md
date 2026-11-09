# DSC_Laya_Model: the admin web UI

The admin UI at `/admin/` has separate sidebar pages for health, access and model configuration. It works with every deployment mode. Only the address differs:

| Run | Address |
|---|---|
| Docker or `run.bat` on this PC | `http://127.0.0.1:29220/admin/` |
| Docker on the deployment box | `http://<box-ip>:29220/admin/` |

If `SERVICE_PORT` is changed, use that port.

## Before you open it

The page needs the admin password: the value of `SERVICE_ADMIN_PASSWORD`.
- Docker Desktop on this device: set it in `.env.compose` next to `docker-compose.yml`, then `docker compose --env-file .env.compose up -d`.
- Deployment device: set it in `.env` next to `docker-compose.yml` (Deploy option 2 copies it), then `docker compose up -d`.
- No Docker: set it in `.env.local` (or `.env`) in this folder and start with `run.bat` (it passes `--dotenv`), or set it in the environment first; a real environment variable wins over both files and `.env.local` wins over `.env`. A plain `python src/app.py` reads neither.
- Empty password = no auth at all and the page answers `Admin password required (set SERVICE_ADMIN_PASSWORD)`. Set
  one to use the page.

## Sign in and service status

Type the admin password and press **Sign in**. A wrong password shows an inline `Wrong admin password` error.
The password stays in `sessionStorage` for this browser tab, so reloading restores the session. **Sign out** clears
the password and any freshly created token. If browser storage is blocked, sign in again after reloading.

The header shows the service address and a health badge with an icon and text: **Healthy**, **Starting**, or
**Unreachable**. Health is checked without authentication every 10 seconds while signed in.
Tiles show models loaded out of the total, replicas, CPU threads, and active tokens.
Use **Theme** to cycle between system, light, and dark appearance; this preference is saved in the browser.

## Tokens

Each calling project gets its own token, so one can be revoked without touching the others.
- **Create:** type a project name (up to 64 characters) and press **Create token**. The token appears once in
  an accent-tinted box with a **Copy** button and the reminder “Shown once. Copy it before you leave.”
  The box is cleared on the next create attempt or sign-out. Clipboard access has a fallback for plain HTTP;
  if both methods are blocked, select the token and copy it manually.
- **Use:** the caller sends it in the `X-Internal-Token` header on every call except `/health`. Citadel: set it as
  Laya project token in `apps/garrison/.env`, then `docker compose up -d` in Citadel (`restart` does not reload `env_file`).
- **List:** the table shows **Project**, **Created**, **Last used**, and **Requests** (authenticated calls so far). Last used is **never** until a token
  authenticates a request, then a relative time; hover a timestamp for its exact local time. The list refreshes
  every 10 seconds. Token values are not shown again.
- **Revoke:** press the red-outlined **Revoke** pill and confirm in the in-app dialog, which names the project.
  **Cancel**, Escape, or a click on the backdrop closes the dialog. The caller gets `401 Invalid token` from then on. Lost
  token = revoke it and create a new one.

Last-used timestamps are Unix seconds or null. They are persisted at most once per minute per token, so they
are approximate. Request counts are added to the file with that same write; uses between writes are
counted in memory, so a crash can lose up to a minute of them. Old token files without the field still load and show **never** until next use. A companion
`tokens.json.lock` file serializes updates across replicas without adding dependencies.

Tokens live in `tokens.json` (`Laya project tokenS_FILE`). In Docker that is the `dsc_laya_model_data` volume at `/data`, so they survive
`docker compose down` and rebuilds. Without Docker it is `tokens.json` in the folder the service starts from. Tokens
are enforced only while `SERVICE_ADMIN_PASSWORD` is set.

## Torch CPU threads

Use the synchronized slider or number field, then **Apply**. Both accept 1 to 256. Apply is disabled and
shows a busy indicator during saving; a short **Saved** confirmation follows. The page states:
“Saved to settings.json. Other replicas apply it within a few seconds.”
- Sets how many CPU threads torch uses for each prediction. In a container with a CPU quota torch still counts the
  host's cores, and more threads than the quota makes each prediction several times slower. Start near the number
  of cores the container may use.
- Range 1 to 256. The field shows the value in use now.
- The change applies at once to the replica that answers the click and is saved to `settings.json` next to
  `tokens.json`. Other replicas pick it up within a few seconds; no restart is needed.
- The saved value beats `SERVICE_TORCH_THREADS` from the env file (`.env.compose` or `.env`). Remove `settings.json` from the `/data` volume to go back
  to the env-file value.
- `GET /health` reports the value in use as `torch_threads`.

## Replicas (read-only)

The replicas tile and muted line **Replicas: N. Change SERVICE_REPLICAS in .env.compose (or .env on the deployment device), then rebuild.** show `SERVICE_REPLICAS`.
The page cannot change it; each replica loads its own copy of the models, so it multiplies RAM. To change it, set `SERVICE_REPLICAS` in `.env.compose` (or `.env` on the deployment device), then:
```bash
docker compose --env-file .env.compose up -d --build
docker compose restart dsc_laya_model_lb
```
`Replicas: unknown` means the variable is not set, which is normal without Docker.

## Problems

| Symptom | Fix |
|---|---|
| Page does not load | Service not running, wrong port, or blocked by a firewall. Test `/health`. |
| `Wrong admin password` | Password differs from `SERVICE_ADMIN_PASSWORD` in the running container. Recreate it after editing `.env.compose` or `.env`. |
| `Admin password required (set SERVICE_ADMIN_PASSWORD)` | The password is empty. Set it and restart. |
| Token lost | Revoke it and create a new one. |
| Thread change not on every replica | Replicas read the saved value every second from the shared `/data` volume; check `torch_threads` in `/health` on each. |
| Page works over plain `http` only | Expected on a LAN. Never expose port 29220 to the internet. |

## Limiting a token to projects

When you create a token you can list the projects it may use (comma separated; empty = all). A request whose `project` is not on the list is refused with `403`. A request without `project` is always allowed. The list cannot be edited later: revoke the token and create a new one.

## Rate limit, expiry, edits, usage and metrics

- **Requests per minute** (optional, when creating or editing a token): over the limit a call gets `429` with `Retry-After`. The count is per replica, so with several replicas behind the balancer the real limit can be up to that many times higher.
- **Expires in (days)** (optional, only when creating): after that the token is refused like a revoked one, and the row shows "Expired". Create a new token to continue.
- **Edit** changes the allowed projects and the rate limit without revoking the token.
- **Usage:** each row shows errors (answers with status 500 or above) and the average time per call. The counters are written at most once a minute.
- **Metrics:** `GET /metrics` (send the admin password in `X-Internal-Token`) returns the same numbers as Prometheus text, one series per token. It reads the shared token file, so any replica answers with the same data. `laya_request_seconds` is a histogram of how long each `/v1` call took on the replica that answers (to the first byte for a streamed reply); it starts from 0 when the replica restarts.
- **Project models** card: the models loaded for a single project on the replica that answered.

## Settings

The **Settings** card changes options without editing `.env.compose`, `.env` or restarting: update-check interval, caches and limits, and similar. Each row shows where its value comes from (saved, environment or default). A value saved here beats the environment variable; **Reset** removes it. Every replica reads the shared settings file, so a change applies within a few seconds. Rows marked "applies after a restart" are read once at start. The admin password, ports, replica count and other Docker settings stay in `.env` or the compose file, because the service cannot change its own container.

## Settings on the admin page

These are edited on the **Settings** card of `/admin/` and saved in `settings.json` beside the tokens, so all replicas share them: `SERVICE_UPDATE_CHECK_HOURS`, `SERVICE_MAX_INFLIGHT`, `SERVICE_SYNC_SECONDS`, `SERVICE_CACHE_SIZE`, `SERVICE_MAX_PROJECT_MODELS`, `SERVICE_PROJECT_WAIT_SECONDS`, `SERVICE_PROJECT_MODELS_DIR` and `SERVICE_WARM` (the last is read at start). A saved value beats the environment variable. The compose file no longer passes them, so a `.env` value only applies if you add the variable under `environment:` of `dsc_laya_model`. Still in `.env`: `SERVICE_ADMIN_PASSWORD`, `SERVICE_PORT`, `SERVICE_REPLICAS`, `SERVICE_TORCH_THREADS` (a starting value for the CPU threads card).

## Updating the models

The **Updates** card shows the installed `dsc_laya_model` package and each weights repository next to the newest one, and flags what is newer. **Check now** looks right away; the replicas also check every `SERVICE_UPDATE_CHECK_HOURS`.

- **Weights:** **Download new weights** fetches the newest revision from the Hub into the shared `dsc_laya_model_models` volume and reloads the models on the replica that took the click. The other replicas notice within `SERVICE_SYNC_SECONDS` and reload them themselves; the models are loaded again, so each replica needs RAM for the old and new copy for a moment. The download is the Hub's current `main`, not the reviewed SHA in `laya.revisions.PINNED_REVISIONS`; set `SERVICE_REVISION=reviewed` to pin a deployment to the reviewed one.
- **Package:** only reported. A running container cannot change its own install: run `uv lock --upgrade-package laya`, then `docker compose --env-file .env.compose up -d --build` and `docker compose restart dsc_laya_model_lb`.

## Connect to this service

The **Connect to this service** card lists the addresses other services can use: the address you opened the page with, the same PC (`127.0.0.1`), another Docker container on the same network (the `_lb` name from the compose file), and a Docker container calling this PC (`host.docker.internal`), then every endpoint with a **Copy** button. Callers send a token from the tokens card in `X-Internal-Token`.

## Try it: send a real request

1. Sign in to `/admin/` and find **Try it**.
2. Paste a service token into the token field. This is the public client token, not the admin password. For a model service, create a token in the existing tokens card if needed; token values cannot be recovered from their stored hashes.
3. Choose an operation, complete its labeled fields and send it. Requests go to this service's public route, with the same token scope, rate/admission limits and cache as a separate client.
4. Read the HTTP status, elapsed time and response. NDJSON/SSE text appears while it arrives; JSON is formatted. HTTP errors, including 401, 422, 429 and 503, keep their response body visible. Use **Cancel** to abort an active request.
5. Copy the curl equivalent to repeat the request elsewhere. The token is always a placeholder; fill it locally. For an upload, provide the indicated input file. Multi-step operations show their follow-up request too.

Authentication choice is **(b), a pasted public token held only in memory**. The playground does not persist it in browser storage, put it in a URL, log it or include it in curl. Sign out or reset the panel to clear it. The existing admin sign-in is separate. A playground run consumes normal service quotas and can generate retained files where the public API normally does so; cancelling the browser fetch does not promise to undo work already accepted by a backend.

The browser caps uploads at **10 MiB**, displayed text at **256 KiB**, and binary audio/image responses at **32 MiB**. Server limits still apply and may be lower. Media uses bounded Blob URLs, revoked when replaced or cleared. Text is rendered as text, never executed as HTML. Audio players become available after the bounded audio download finishes. No new environment setting is needed.

### This service's request

Ask sends `text` and a questions JSON object to `POST /v1/ask`, with the selected strategy and optional project. Streaming appends one NDJSON answer event per question. The operation menu also exposes question validation, vote combination and the strategy list.

## Downloaded weights

Weights load only from downloaded files. An empty installation still serves `/admin/` and `/health` with `ready: false`; inference returns 503. Open `/admin/` → **Updates** → **Download** before the first inference. Checks run online without downloading; only an admin Download replaces weights and reloads replicas.

Alternatively, use the authenticated admin download route (replace the password placeholder):

```bash
curl -X POST http://127.0.0.1:29220/admin/updates/weights -H "X-Internal-Token: <ADMIN_PASSWORD>"
```

The Updates card shows the downloaded and online versions and reports when a newer version is available. A pinned file or local import has no online newer-version comparison. Download runs only when requested by an admin.

## Admin pages and navigation

Open `/admin/` and sign in. Overview opens first; each sidebar destination has its own URL fragment and supports browser Back/Forward and direct links:

- `#overview`: health, model readiness, replicas, active tokens and request counts.
- `#playground`: service-specific request forms and streamed or media responses.
- `#tokens`: create, edit, expire and revoke scoped access tokens.
- `#runtime`: CPU threads where supported, replica information and live/restart settings.
- `#updates`: check versions and explicitly download weights.
- `#connections`: caller addresses and public endpoints.
- `#models` and `#voices`: project models and cloned voices, shown only when supported.

Collapse/Expand switches between labeled sidebar and icon rail on desktop and mobile. The non-secret sidebar preference is saved per service in this browser. Navigation preserves form drafts and active Playground requests; sign-out still clears credentials and cancels work. Page names appear once in the main header. No preview data or cross-service selector ships in the real admin UI. Existing authentication, quotas, settings precedence and explicit-download policy remain in effect.
