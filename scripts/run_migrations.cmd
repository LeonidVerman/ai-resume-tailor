@echo off
:: scripts\run_migrations.cmd
::
:: Run Alembic database migrations for the backend.
::
:: Usage:
::   scripts\run_migrations.cmd                    :: upgrade to head (default)
::   scripts\run_migrations.cmd downgrade -1       :: downgrade one step
::   scripts\run_migrations.cmd current            :: show current revision
::
:: Prerequisites:
::   - PostgreSQL running and DATABASE_URL set in .env or backend\.env
::   - pip install -e backend\

setlocal EnableDelayedExpansion

:: Resolve repo root
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
pushd "%REPO_ROOT%"
set "REPO_ROOT=%CD%"
popd

:: Load .env if present (repo root first, then backend\.env)
if exist "%REPO_ROOT%\.env" (
    for /f "usebackq tokens=* eol=#" %%L in ("%REPO_ROOT%\.env") do (
        set "%%L" 2>nul
    )
) else if exist "%REPO_ROOT%\backend\.env" (
    for /f "usebackq tokens=* eol=#" %%L in ("%REPO_ROOT%\backend\.env") do (
        set "%%L" 2>nul
    )
)

:: Validate DATABASE_URL
if "%DATABASE_URL%"=="" (
    echo [run_migrations] ERROR: DATABASE_URL is not set.
    echo   Set it in .env or set it before running this script.
    exit /b 1
)

:: Build action string from arguments (default: upgrade head)
set "ACTION=%*"
if "%ACTION%"=="" set "ACTION=upgrade head"

echo [run_migrations] Running: alembic %ACTION%
cd /d "%REPO_ROOT%"
python -m alembic -c backend\alembic.ini %ACTION%
echo [run_migrations] Done.
