# DSC_Laya_Model

Laya prediction models served over HTTP, behind the shared model-service admin page, tokens and settings. Port 29220, env prefix `SERVICE_`, token prefix `laya_`.

## Agent instructions
This file is the one instruction file for every coding agent; `CLAUDE.md` only imports it (`@AGENTS.md`). Edit this file, never `CLAUDE.md`.

## Commands (from this folder, venv `.venv`, managed by uv)
- Install: `uv sync` (`winget install astral-sh.uv` if uv is missing)
- Test: `uv run pytest -q`. `tests/test_admin_ui.py` needs Playwright and its browser and is skipped without it. On Windows, if uv fails with "cannot move the file to a different disk drive", set `UV_CACHE_DIR` to a folder on the same drive and `UV_LINK_MODE=copy`.
- Run: `run.bat run`, or `uv run python src/app.py` (add `--dotenv` to load `.env.local` and `.env`), or `docker compose --env-file .env.compose up -d --build`

## Rules
- Load downloaded weights only. Startup, warm-up and inference never download; only the authenticated Updates Download callback may fetch weights. Missing weights keep health/admin available and inference returns 503 with an admin Download hint.
- Follow the `model-service-parity` skill and `AI/ModelServices/_docs/SHARED-FEATURES.md` (what must match in every model service) and `AI/ModelServices/_docs/SERVICE-SPECIFIC.md` (what differs on purpose). A change to a shared feature goes to every service; a new difference goes into `SERVICE-SPECIFIC.md` in the same change.
- `web/index.html`, `src/tokens.py`, `src/settings.py` and `.dockerignore` are copies shared with the other model services. Change one, copy it to the others, run `python tools/check_sync.py` in each repo.
- `src/updates.py` is this service's own (it updates the models in its registry and syncs weights between replicas), so it is the one copy `check_sync.py` skips.
- Answers come from the `laya` package through `src/registry.py` and `src/strategies.py`; their tests are `tests/test_projects.py` and `tests/test_strategies.py`.
- CPU threads are `torch_threads`, not `num_thread`. `/v1/ask` is limited by `max_inflight` (429); project models wait up to `project_wait_seconds`, then 503.
- Behaviour changes update `README.md`, `deployment.md`, `docs/guide-web-ui.md`, `.env.example` and `CHANGELOG.md` in the same commit.
- Never read or print `.env`, `.env.local`, `.env.compose`, `tokens.json` or `settings.json`.
- Commit with the noreply author email `214214011+scutiii2@users.noreply.github.com` (another address is rejected on push). Push `main`, then run `python tools/publish_live.py`. Build Docker only when the Dockerfile or the dependencies changed.
