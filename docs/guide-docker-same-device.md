# LayaModel: Docker on the same device

Run the service in Docker on the machine you work on (your PC), so other programs on that machine can call it.

## Requirements
- Docker with Compose v2 (Docker Desktop on Windows/macOS).
- RAM for the models **per replica**. Keep `LAYA_REPLICAS=1` on a PC.
- Internet on the first start (weights download). CPU only.

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
