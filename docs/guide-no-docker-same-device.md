# LayaModel: without Docker, same device

Run the service directly with Python. One process, no load balancer, listens on `127.0.0.1:9120`.

## Requirements
- Python 3.11 or newer.
- Internet on the first start (weights download to the Hugging Face cache).
- RAM for both models (one copy).

## Windows
Double-click `run.bat` or run it in a terminal from `LayaModel/`. It runs `uv sync` to create or update `.venv`
(needs uv: `python -m pip install uv`), then starts `python src/app.py`.

## Linux / macOS
```bash
cd LayaModel
uv sync
uv run python src/app.py
```
`uv.lock` already pins torch to the CPU-only index on Linux and Windows, so no extra step is needed for a small install.

## Settings
`run.bat` and `app.py` read the real environment, **not** `.env`. Set variables before starting:

| Variable | Default | Effect |
|---|---|---|
| `LAYA_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` only if other devices must reach it. |
| `LAYA_PORT` | `9120` | Port. |
| `LAYA_ADMIN_PASSWORD` | empty | Unlocks `/admin/` and requires tokens on every call but `/health`. Empty = no auth. |
| `LAYA_WARM` | `1` | Load both models at start. |
| `LAYA_TORCH_THREADS` | torch decides | CPU thread cap. |
| `LAYA_OFFLINE` | unset | `1` uses only downloaded weights. |

PowerShell: `$env:LAYA_ADMIN_PASSWORD = "secret"; .\run.bat`
Bash: `LAYA_ADMIN_PASSWORD=secret python src/app.py`

## Check
```bash
curl http://127.0.0.1:9120/health
```
Expect `{"status": "ok", ...}`.

## Connect Citadel
- Garrison outside Docker: in `Citadel/apps/garrison/.env` set `LAYA_SERVICE_URL=http://127.0.0.1:9120` and
  `LAYA_TOKEN` (made on `http://127.0.0.1:9120/admin/` when a password is set).
- Citadel in Docker, Laya on the host: start Laya with `LAYA_HOST=0.0.0.0` and point garrison at
  `http://host.docker.internal:9120`. Citadel's compose sets `LAYA_SERVICE_URL` itself, so you must override it in
  garrison's environment.

## Stop
Ctrl+C in the terminal. Do not run this and the Docker stack together: both use port 9120.

## Tests
`uv run pytest`.
