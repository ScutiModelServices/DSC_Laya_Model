# DSC_Laya_Model: Docker on the same device

Run the service in Docker on the machine you work on (your PC), so other programs on that machine can call it.

## Requirements

- Docker with Compose v2 (Docker Desktop on Windows/macOS).
- RAM for the models **per replica** (each replica loads its own copy), so `SERVICE_REPLICAS` multiplies memory use. Keep `SERVICE_REPLICAS=1` on a PC.
- Network access on the first start to download the weights (`SERVICE_OFFLINE` skips it once the weights are in the `dsc_laya_model_models` volume; see [Settings](#settings)).
- CPU only. The Dockerfile installs the CPU build of torch and fails the build if a CUDA or ROCm build is found.

## Steps

1. Configure. In `DSC_Laya_Model/`:
   ```bash
   cp .env.example .env
   ```
   Set `SERVICE_ADMIN_PASSWORD` (empty = no auth, fine for local-only use). Leave `SERVICE_PORT=29220`, `SERVICE_REPLICAS=1`.
2. Start:
   ```bash
   docker compose up -d --build
   ```
   First start downloads the weights into the `dsc_laya_model_models` volume. `dsc_laya_model_lb` only starts after a replica is healthy
   (up to 120 s grace). Watch with `docker compose ps` and `docker compose logs -f dsc_laya_model`.
3. Check:
   ```bash
   curl http://127.0.0.1:29220/health
   ```
   Expect `{"status": "ok", ...}` with both models `true`.
4. Tokens: if you set a password, open `http://127.0.0.1:29220/admin/` and make a token for each caller.
5. Callers connect to the published port. Laya does not depend on any caller; the caller holds the URL and token:
   - Caller on the host: `http://127.0.0.1:29220`.
   - Caller in another Docker stack on the same host: `http://host.docker.internal:29220`.
   - Citadel: set Laya connection URL and Laya project token on the Citadel side.

## Project checkpoints

Set the **Project models folder** on the admin Settings card to `/models/projects` to enable finished local typed-decisions
checkpoints. Leave it empty to disable project loading. Copy each complete directory into the shared volume:

```bash
docker compose up -d --build
docker compose exec dsc_laya_model mkdir -p /models/projects
docker compose cp ./garrison laya:/models/projects/garrison
```

Send `"project": "garrison"` with `/v1/ask`. Names match `^[a-z0-9][a-z0-9_-]{0,47}$` (otherwise 422).
Only TYPED changes; English stays base. Missing artifacts fall back; `model`, `fallback` and `project_used` report the selection.
`SERVICE_MAX_PROJECT_MODELS=2` caps resident project engines per replica; base engines stay loaded separately.
See [checkpoint requirements](../README.md#project-checkpoints). `/health` lists loaded `project_models`.
Hub weight downloads leave local artifacts alone; restart after replacing one already loaded.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `SERVICE_REPLICAS` | `1` | Number of `dsc_laya_model` containers. Read by Compose, not the service. |
| `SERVICE_MEM_LIMIT` | `0` (no cap) | RAM cap per container, for example `4g`. An out-of-memory kill then stays in the container instead of starving the host. Read by Compose. |
| `SERVICE_PORT` | `29220` | Host port `dsc_laya_model_lb` publishes. Inside the containers the service always listens on 29220. |
| `SERVICE_ADMIN_PASSWORD` | empty | Password for the `/admin/` page, where per-project tokens are made. When set, every call except `/health` must send one of those tokens as `X-Internal-Token` (Garrison: Laya project token in `apps/garrison/.env`). Empty = no auth. |
| `SERVICE_WARM` | `1` | Load both models at start (also on the Settings card). With it off the first request pays the load time. |
| `SERVICE_TORCH_THREADS` | torch decides | Cap on torch CPU threads; `/health` reports the value in use, `/admin/` can change it and saves the value (other replicas apply it within a few seconds). Use the number of physical cores (`lscpu`: cores x sockets), not logical CPUs; more threads than cores slows every call. |
| `SERVICE_UPDATE_CHECK_HOURS` | `6` | How often each replica looks for a newer `dsc_laya_model` package (PyPI) and newer weights (Hugging Face). It only reports; `0` turns the timer off. Off too with `SERVICE_OFFLINE=1`. |
| `SERVICE_MAX_INFLIGHT` | `0` | `/v1/ask` calls each replica lets wait for the models at once; more get `429` with `Retry-After: 5`. `0` means 4. |
| `SERVICE_SYNC_SECONDS` | `10` | How often a replica looks for weights another replica downloaded (a marker file in the models volume) and reloads its models. `0` turns it off. |
| `SERVICE_CACHE_SIZE` | `256` | Answers each replica keeps for identical `/v1/ask` requests (response headers `X-Laya-Cache` and `X-Cache`: `hit` or `miss`). Cleared by new weights and by changed project files. `0` turns it off. |
| `SERVICE_OFFLINE` | unset | `1` uses only already-downloaded weights. Neither compose file passes it: add it under `environment:` of `dsc_laya_model` to use it. |
| `SERVICE_PROJECT_MODELS_DIR` | unset | Local project-checkpoint root; empty disables it. With Docker use `/models/projects` inside the shared `dsc_laya_model_models` volume. |
| `SERVICE_MAX_PROJECT_MODELS` | `2` | Maximum resident project engines per replica (minimum `1`); LRU evicts idle engines only. Base models are never evicted. |
| `SERVICE_PROJECT_WAIT_SECONDS` | `30` | How long a request waits for a free project slot before a `503` with `Retry-After: 5`. |

Put the variables in a `.env` next to the compose file that runs the stack (this folder).

## Callers

A caller needs only the URL and a token. From a container on the same host use `http://host.docker.internal:29220`;
Citadel's legion does this through Laya connection URL in `apps/legion/.env`. Citadel's compose does not start or build
this service, so the two folders can sit anywhere and be started in any order.

## First start

1. The build installs torch and the service. The image has no weights.
2. Each replica downloads the weights into the `dsc_laya_model_models` volume the first time. Replicas
   share the volume, so later starts and extra replicas reuse the download.
3. `dsc_laya_model_lb` starts only after a replica passes its health check. The check allows 120 s (`start_period`) before
   failures count, so a slow first download can leave the stack `unhealthy`; run `docker compose ps` and wait, or
   raise `start_period`.

## Check it works

```bash
curl http://127.0.0.1:29220/health
```

Expect `{"status": "ok", "models": {...}}` with both models `true` once warmed. `/health` needs no token.

```bash
curl -X POST http://127.0.0.1:29220/v1/validate -H "Content-Type: application/json" -H "X-Internal-Token: <token>" -d "{\"questions\": []}"
```

A 422 means the service is up and checking requests (an empty question set is invalid). Omit the token header when
`SERVICE_ADMIN_PASSWORD` is empty.

## Daily use

```bash
docker compose ps          # state
docker compose logs -f dsc_laya_model
docker compose down        # stop (weights volume is kept)
docker compose up -d --build && docker compose restart dsc_laya_model_lb   # after a code change
```

## Problems

| Symptom | Fix |
|---|---|
| `address already in use` on 29220 | Another Laya (a caller's own stack or `run.bat`) is running. Stop it or change `SERVICE_PORT`. |
| Replicas restart in a loop | Out of RAM. Lower `SERVICE_REPLICAS`. |
| 401 Invalid token | Caller token missing or revoked; make one on `/admin/`. |
| `dsc_laya_model_lb` never starts | No replica is healthy yet (first download, or out of memory). Check `docker compose logs dsc_laya_model`. |
| Garrison reports that Laya is not configured | Its Laya connection URL is empty (running outside Compose and not set). Configure it through Citadel's existing consumer settings. |
| Garrison: "could not be reached" | Service not started, or Garrison's Laya connection URL does not point at it. |
| `bind: address already in use` on 29220 | Another process holds the port. Stop it, or set `SERVICE_PORT`. |
| nginx 502 after scaling | `dsc_laya_model_lb` not restarted after the replica count changed. |
| Offline start fails to load a model | `SERVICE_OFFLINE=1` but the weights are not in the `dsc_laya_model_models` volume yet. |

The load balancer also joins external `citadel_core` as `dsc_laya_model_lb`. Start the Citadel stack first so that network exists; tool services on it use `http://dsc_laya_model_lb:29220`. Docker project and API image are `dsc_laya_model` and `dsc_laya_model:latest`; persistent volumes have explicit `dsc_laya_model_*` names. Volume migration and rollback instructions are in [CHANGELOG.md](../CHANGELOG.md).
