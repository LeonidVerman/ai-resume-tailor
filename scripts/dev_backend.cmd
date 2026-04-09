@echo off
:: scripts\dev_backend.cmd
::
:: Start the FastAPI backend in development mode with hot reload.
::
:: Usage:
::   scripts\dev_backend.cmd
::
:: Prerequisites:
::   - PostgreSQL running (docker compose up postgres  OR  external DB)
::   - .env configured with DATABASE_URL and OPENAI_API_KEY
::   - pip install -e backend\

setlocal EnableDelayedExpansion

:: Resolve repo root (parent of this script's directory)
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
pushd "%REPO_ROOT%"
set "REPO_ROOT=%CD%"
popd

:: Load .env if present
if exist "%REPO_ROOT%\.env" (
    for /f "usebackq tokens=* eol=#" %%L in ("%REPO_ROOT%\.env") do (
        set "%%L" 2>nul
    )
)

cd /d "%REPO_ROOT%"

echo [dev_backend] Starting FastAPI backend on http://localhost:8000
echo [dev_backend] API docs: http://localhost:8000/docs
echo [dev_backend] Health:   http://localhost:8000/api/v1/health
echo.

uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000 --log-level info
