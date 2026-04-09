@echo off
:: scripts\dev_frontend.cmd
::
:: Start the Next.js frontend in development mode.
::
:: Usage:
::   scripts\dev_frontend.cmd

setlocal

:: Resolve repo root
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
pushd "%REPO_ROOT%"
set "REPO_ROOT=%CD%"
popd

set "FRONTEND_DIR=%REPO_ROOT%\frontend"

if not exist "%FRONTEND_DIR%\package.json" (
    echo [dev_frontend] Error: frontend\package.json not found.
    exit /b 1
)

if not exist "%FRONTEND_DIR%\node_modules" (
    echo [dev_frontend] Installing frontend dependencies...
    cd /d "%FRONTEND_DIR%" && npm install
)

echo [dev_frontend] Starting Next.js development server on http://localhost:3000
cd /d "%FRONTEND_DIR%" && npm run dev
