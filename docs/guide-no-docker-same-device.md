# DSC_Laya_Model: without Docker, same device

Run the service directly with Python. One process, no load balancer, listens on `127.0.0.1:29220`.

## Requirements
- Python 3.11 or newer.
- Internet on the first start (weights download to the Hugging Face cache).
- RAM for both models (one copy).

## Windows
Double-click `run.bat` or run it in a terminal from `DSC_Laya_Model/`. It runs `uv sync` to create or update `.venv`
(needs uv: `winget install astral-sh.uv`), then starts `python src/app.py`.

## Linux / macOS
```bash
cd DSC_Laya_Model
uv sync
uv run python src/app.py
```
`uv.lock` already pins torch to the CPU-only index on Linux and Windows, so no extra step is needed for a small install.

## Settings
`run.bat` loads `.env` in this folder (copy `.env.example`) through `--dotenv` and prints the names it loaded, never the values; a real environment variable wins over it. A plain `python src/app.py` does not read `.env`. Set variables in `.env` or in the environment before starting:

| Variable | Default | Effect |
|---|---|---|
| `SERVICE_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` only if other devices must reach it. |
| `SERVICE_PORT` | `29220` | Port. |
| `SERVICE_ADMIN_PASSWORD` | empty | Unlocks `/admin/` and requires tokens on every call but `/health`. Empty = no auth. |
| `SERVICE_WARM` | `1` | Load both models at start. |
| `SERVICE_TORCH_THREADS` | torch decides | CPU thread cap. |
| `SERVICE_OFFLINE` | unset | `1` uses only downloaded weights. |
| `SERVICE_PROJECT_MODELS_DIR` | unset | Root containing finished `<project>/` checkpoints; empty disables them. |
| `SERVICE_MAX_PROJECT_MODELS` | `2` | Maximum resident project engines (minimum `1`); base engines stay separate. |
| `SERVICE_PROJECT_WAIT_SECONDS` | `30` | Wait for a free project slot before answering `503`. |

PowerShell: `$env:SERVICE_ADMIN_PASSWORD = "secret"; .\run.bat`
Bash: `SERVICE_ADMIN_PASSWORD=secret python src/app.py`

## Project checkpoints

Train typed-decisions elsewhere, then copy the complete checkpoint to, for example, `D:/models/garrison/`.
Set `$env:SERVICE_PROJECT_MODELS_DIR = "D:/models"` in PowerShell before starting; Bash uses
`SERVICE_PROJECT_MODELS_DIR=/path/to/models uv run python src/app.py`.
The [required checkpoint files](../README.md#project-checkpoints) must be present; no model downloads are made
for projects, even with `SERVICE_OFFLINE=1`.

Send `"project": "garrison"` with `/v1/ask`. Names match `^[a-z0-9][a-z0-9_-]{0,47}$` (otherwise 422).
Only TYPED uses the project checkpoint; English stays base. Missing artifacts fall back; responses and usage
calls report `model` and `fallback`. `/health` lists loaded `project_models`. Restart after replacing a loaded
checkpoint; **Download new weights** only handles Hub models.

## Check
```bash
curl http://127.0.0.1:29220/health
```
Expect `{"status": "ok", ...}`.

## Connect Citadel
- Garrison outside Docker: in `Citadel/apps/garrison/.env` set Laya connection URL to `http://127.0.0.1:29220` and
  Laya project token (made on `http://127.0.0.1:29220/admin/` when a password is set).
- Citadel in Docker, Laya on the host: start Laya with `SERVICE_HOST=0.0.0.0` and point garrison at
  `http://host.docker.internal:29220`. Citadel's compose sets Laya connection URL itself, so you must override it in
  garrison's environment.

## Stop
Ctrl+C in the terminal. Do not run this and the Docker stack together: both use port 29220.

## Tests
`uv run pytest`.
