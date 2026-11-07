# LayaModel: Docker on the same device

Run the service in Docker on the machine you work on (your PC), so other programs on that machine can call it.

## Requirements

- Docker with Compose v2 (Docker Desktop on Windows/macOS).
- RAM for the models **per replica** (each replica loads its own copy), so `LAYA_REPLICAS` multiplies memory use. Keep `LAYA_REPLICAS=1` on a PC.
- Network access on the first start to download the weights (`LAYA_OFFLINE` skips it once the weights are in the `models` volume; see [Settings](#settings)).
- CPU only. The Dockerfile installs the CPU build of torch and fails the build if a CUDA or ROCm build is found.

## Steps

1. Configure. In `LayaModel/`:
   ```bash
   cp .env.example .env
   ```
   Set `LAYA_ADMIN_PASSWORD` (empty = no auth, fine for local-only use). Leave `LAYA_PORT=9120`, `LAYA_REPLICAS=1`.
2. Start:
   ```bash
   docker compose up -d --build
   ```
   First start downloads the weights into the `models` volume. `laya_lb` only starts after a replica is healthy
   (up to 120 s grace). Watch with `docker compose ps` and `docker compose logs -f laya`.
3. Check:
   ```bash
   curl http://127.0.0.1:9120/health
   ```
   Expect `{"status": "ok", ...}` with both models `true`.
4. Tokens: if you set a password, open `http://127.0.0.1:9120/admin/` and make a token for each caller.
5. Callers connect to the published port. Laya does not depend on any caller; the caller holds the URL and token:
   - Caller on the host: `http://127.0.0.1:9120`.
   - Caller in another Docker stack on the same host: `http://host.docker.internal:9120`.
   - Citadel: set `LAYA_SERVICE_URL` and `LAYA_TOKEN` on the Citadel side.

## Project checkpoints

Set the **Project models folder** on the admin Settings card to `/models/projects` to enable finished local typed-decisions
checkpoints. Leave it empty to disable project loading. Copy each complete directory into the shared volume:

```bash
docker compose up -d --build
docker compose exec laya mkdir -p /models/projects
docker compose cp ./garrison laya:/models/projects/garrison
```

Send `"project": "garrison"` with `/v1/ask`. Names match `^[a-z0-9][a-z0-9_-]{0,47}$` (otherwise 422).
Only TYPED changes; English stays base. Missing artifacts fall back; `model`, `fallback` and `project_used` report the selection.
`LAYA_MAX_PROJECT_MODELS=2` caps resident project engines per replica; base engines stay loaded separately.
See [checkpoint requirements](../README.md#project-checkpoints). `/health` lists loaded `project_models`.
Hub weight downloads leave local artifacts alone; restart after replacing one already loaded.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `LAYA_REPLICAS` | `1` | Number of `laya` containers. Read by Compose, not the service. |
| `LAYA_MEM_LIMIT` | `0` (no cap) | RAM cap per container, for example `4g`. An out-of-memory kill then stays in the container instead of starving the host. Read by Compose. |
| `LAYA_PORT` | `9120` | Host port `laya_lb` publishes. Inside the containers the service always listens on 9120. |
| `LAYA_ADMIN_PASSWORD` | empty | Password for the `/admin/` page, where per-project tokens are made. When set, every call except `/health` must send one of those tokens as `X-Internal-Token` (Garrison: `LAYA_TOKEN` in `apps/garrison/.env`). Empty = no auth. |
| `LAYA_WARM` | `1` | Load both models at start (also on the Settings card). With it off the first request pays the load time. |
| `LAYA_TORCH_THREADS` | torch decides | Cap on torch CPU threads; `/health` reports the value in use, `/admin/` can change it and saves the value (other replicas apply it within a few seconds). Use the number of physical cores (`lscpu`: cores x sockets), not logical CPUs; more threads than cores slows every call. |
| `LAYA_UPDATE_CHECK_HOURS` | `6` | How often each replica looks for a newer `laya` package (PyPI) and newer weights (Hugging Face). It only reports; `0` turns the timer off. Off too with `LAYA_OFFLINE=1`. |
| `LAYA_MAX_INFLIGHT` | `0` | `/v1/ask` calls each replica lets wait for the models at once; more get `429` with `Retry-After: 5`. `0` means 4. |
| `LAYA_SYNC_SECONDS` | `10` | How often a replica looks for weights another replica downloaded (a marker file in the models volume) and reloads its models. `0` turns it off. |
| `LAYA_CACHE_SIZE` | `256` | Answers each replica keeps for identical `/v1/ask` requests (response headers `X-Laya-Cache` and `X-Cache`: `hit` or `miss`). Cleared by new weights and by changed project files. `0` turns it off. |
| `LAYA_OFFLINE` | unset | `1` uses only already-downloaded weights. Neither compose file passes it: add it under `environment:` of `laya` to use it. |
| `LAYA_PROJECT_MODELS_DIR` | unset | Local project-checkpoint root; empty disables it. With Docker use `/models/projects` inside the shared `models` volume. |
| `LAYA_MAX_PROJECT_MODELS` | `2` | Maximum resident project engines per replica (minimum `1`); LRU evicts idle engines only. Base models are never evicted. |
| `LAYA_PROJECT_WAIT_SECONDS` | `30` | How long a request waits for a free project slot before a `503` with `Retry-After: 5`. |

Put the variables in a `.env` next to the compose file that runs the stack (this folder).

## Callers

A caller needs only the URL and a token. From a container on the same host use `http://host.docker.internal:9120`;
Citadel's legion does this through `LAYA_SERVICE_URL` in `apps/legion/.env`. Citadel's compose does not start or build
this service, so the two folders can sit anywhere and be started in any order.

## First start

1. The build installs torch and the service. The image has no weights.
2. Each replica downloads the weights into the `models` volume the first time. Replicas
   share the volume, so later starts and extra replicas reuse the download.
3. `laya_lb` starts only after a replica passes its health check. The check allows 120 s (`start_period`) before
   failures count, so a slow first download can leave the stack `unhealthy`; run `docker compose ps` and wait, or
   raise `start_period`.

## Check it works

```bash
curl http://127.0.0.1:9120/health
```

Expect `{"status": "ok", "models": {...}}` with both models `true` once warmed. `/health` needs no token.

```bash
curl -X POST http://127.0.0.1:9120/v1/validate -H "Content-Type: application/json" -H "X-Internal-Token: <token>" -d "{\"questions\": []}"
```

A 422 means the service is up and checking requests (an empty question set is invalid). Omit the token header when
`LAYA_ADMIN_PASSWORD` is empty.

## Daily use

```bash
docker compose ps          # state
docker compose logs -f laya
docker compose down        # stop (weights volume is kept)
docker compose up -d --build && docker compose restart laya_lb   # after a code change
```

## Problems

| Symptom | Fix |
|---|---|
| `address already in use` on 9120 | Another Laya (a caller's own stack or `run.bat`) is running. Stop it or change `LAYA_PORT`. |
| Replicas restart in a loop | Out of RAM. Lower `LAYA_REPLICAS`. |
| 401 Invalid token | Caller token missing or revoked; make one on `/admin/`. |
| `laya_lb` never starts | No replica is healthy yet (first download, or out of memory). Check `docker compose logs laya`. |
| Garrison: "Laya is not configured: set LAYA_SERVICE_URL" | `LAYA_SERVICE_URL` is empty (running outside Compose and not set). |
| Garrison: "could not be reached" | Service not started, or Garrison's `LAYA_SERVICE_URL` does not point at it. |
| `bind: address already in use` on 9120 | Another process holds the port. Stop it, or set `LAYA_PORT`. |
| nginx 502 after scaling | `laya_lb` not restarted after the replica count changed. |
| Offline start fails to load a model | `LAYA_OFFLINE=1` but the weights are not in the `models` volume yet. |
