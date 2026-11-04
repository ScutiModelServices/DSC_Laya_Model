# Deploying LayaModel

LayaModel is one HTTP service (port 9120) that owns the Laya models. It runs as `laya` replicas behind an nginx
load balancer, `laya_lb`. Callers use `http://<host>:9120`; they never reach a replica directly.

It is its own Docker stack. Other projects (Citadel's legion, for example) call it over HTTP and never build or start it.

## Requirements

- Docker with Compose v2.
- RAM for the models **per replica** (each replica loads its own copy), so `LAYA_REPLICAS` multiplies memory use.
- Network access on the first start to download the weights (see Offline).
- CPU only. The Dockerfile installs the CPU build of torch and fails the build if a CUDA or ROCm build is found.

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

## Run

The stack does not depend on Citadel or any other project. A caller reaches it at
`http://<host>:9120`, or from another container on the same host at `http://host.docker.internal:9120`.

```bash
docker compose up -d --build
```

Without Docker: `run.bat` (runs `uv sync` into `.venv`, listens on `127.0.0.1:9120`).

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

## Scaling

### Local project checkpoints

Train typed-decisions elsewhere and copy the complete checkpoint directory to the host. With Docker,
set the **Project models folder** on the admin Settings card to `/models/projects` and optionally the project model limit to 2; no restart is needed. Copy a project directory into the existing shared volume:

```bash
docker compose up -d --build
docker compose exec laya mkdir -p /models/projects
docker compose cp ./garrison laya:/models/projects/garrison
```

The checkpoint needs `rl_agent_config.json`, `model.safetensors`, `encoder/config.json` and
`tokenizer/tokenizer.json`, plus anything else its `laya.load(path)` requires. Publish complete directories before
sending traffic. All replicas see the same files but each has its own RAM cap. For a stack owned elsewhere,
pass these environment settings to its `laya` service and reuse its existing models volume.

Call `/v1/ask` with `"project": "garrison"`. Only TYPED is replaced; English keeps its base engine. Invalid names
return 422; absent or incomplete artifacts fall back to base typed-decisions. `model` and `fallback` appear in
the response and each usage-log call. `/health` adds `project_models`; base-model `ready` is unchanged.
Omitting the root disables project loading, including in offline mode. Hub weight updates never touch these
artifacts. Restart replicas after replacing an already loaded project checkpoint.

### Replica count

```bash
LAYA_REPLICAS=3 docker compose up -d --build
docker compose restart laya_lb
```

The restart is required: nginx resolves the replica addresses once at start, so it does not see new or removed
replicas until it restarts. `least_conn` balances; a request that errors or times out on one replica is retried once
on another (safe because `/v1/ask` is a pure function of its body).

## Update

```bash
docker compose up -d --build
docker compose restart laya_lb
```

Rebuilding replaces the replicas, so restart `laya_lb` afterwards. The weights volume survives rebuilds.

### Updating the models from `/admin/`

The **Updates** card shows the installed `laya` package and each weights repository next to the newest one, and flags what is newer. **Check now** looks right away; the replicas also check every `LAYA_UPDATE_CHECK_HOURS`.

- **Weights:** **Download new weights** fetches the newest revision from the Hub into the shared `models` volume and reloads the models on the replica that took the click. The other replicas notice within `LAYA_SYNC_SECONDS` and reload them themselves; the models are loaded again, so each replica needs RAM for the old and new copy for a moment. The download is the Hub's current `main`, not the reviewed SHA in `laya.revisions.PINNED_REVISIONS`; set `LAYA_REVISION=reviewed` to pin a deployment to the reviewed one.
- **Package:** only reported. A running container cannot change its own install: run `uv lock --upgrade-package laya`, then `docker compose up -d --build` and `docker compose restart laya_lb`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `laya_lb` never starts | No replica is healthy yet (first download, or out of memory). Check `docker compose logs laya`. |
| Replicas restart in a loop | Out of RAM for the models. Lower `LAYA_REPLICAS`. |
| Garrison: "Laya is not configured: set LAYA_SERVICE_URL" | `LAYA_SERVICE_URL` is empty (running outside Compose and not set). |
| Garrison: "could not be reached" | Service not started, or Garrison's `LAYA_SERVICE_URL` does not point at it. |
| 401 "Invalid token" from every call but `/health` | the caller sends no project token, or a revoked one. |
| `bind: address already in use` on 9120 | Another process holds the port. Stop it, or set `LAYA_PORT`. |
| nginx 502 after scaling | `laya_lb` not restarted after the replica count changed. |
| Offline start fails to load a model | `LAYA_OFFLINE=1` but the weights are not in the `models` volume yet. |

### Releasing

A plain publish (`tools\deploy.bat` option 1) only updates the `live` branch, so it is fine for test deploys. A release is a separate step: set `VERSION` (and `pyproject.toml` where it has a version, to the same number), commit, then run `tools\deploy.bat` option R or `py tools\publish_live.py --release`. That publishes to `live` and tags the `main` commit `vX.Y.Z` and the live commit `live-vX.Y.Z`. A version already released with different live files is refused, so bump `VERSION`. While a version is not officially released yet, add `--replace` to move its tags.

`tools\make_zip.bat` then builds `zip_versions\LayaModel-<VERSION>.zip` (no `tools/`, no git-ignored files, no older ZIPs; `.env.example` is included) and opens GitHub's new-release page for the tag.

## Settings on the admin page

These are edited on the **Settings** card of `/admin/` and saved in `settings.json` beside the tokens, so all replicas share them: `LAYA_UPDATE_CHECK_HOURS`, `LAYA_MAX_INFLIGHT`, `LAYA_SYNC_SECONDS`, `LAYA_CACHE_SIZE`, `LAYA_MAX_PROJECT_MODELS`, `LAYA_PROJECT_WAIT_SECONDS`, `LAYA_PROJECT_MODELS_DIR` and `LAYA_WARM` (the last is read at start). A saved value beats the environment variable. The compose file no longer passes them, so a `.env` value only applies if you add the variable under `environment:` of `laya`. Still in `.env`: `LAYA_ADMIN_PASSWORD`, `LAYA_PORT`, `LAYA_REPLICAS`, `LAYA_TORCH_THREADS` (a starting value for the CPU threads card) and `LAYA_OFFLINE`.


## Verify with Try it

After deployment, open `/admin/` and use **Try it** with a public service token to exercise the deployed public route. It uses normal scope, admission/rate limits and cache behavior, so busy or unauthorized requests remain visible as HTTP errors. No new environment setting or admin-to-public auth bypass is introduced. Deploy the complete `web/` directory, including `playground.js`. See [the web UI guide](docs/guide-web-ui.md#try-it-send-a-real-request) for input formats, limits and token handling.
