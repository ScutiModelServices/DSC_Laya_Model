@echo off
REM Starts the Laya service on port 9120 (syncs .venv with uv on each run).
REM LABEL: Laya Service
REM DESCRIPTION: Shared HTTP service that owns the Laya models for every project.

cd /d "%~dp0"

uv sync
if errorlevel 1 (
    echo uv sync failed. Install uv: winget install astral-sh.uv
    pause
    exit /b 1
)
call .venv\Scripts\activate

python src/app.py
pause
