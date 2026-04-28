@echo off
:: tests/test_rendering.cmd
::
:: Run the deterministic rendering test for matched (classification + generation) pairs.
::
:: Usage (from repo root):
::   tests\test_rendering.cmd           :: render all matched samples
::   tests\test_rendering.cmd 1         :: render sample with numeric prefix 1
::   tests\test_rendering.cmd 1-Leonid  :: render sample matching filename fragment
::
:: The optional argument is forwarded to tests\rendering\render_samples.py.

setlocal

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
set "RENDER_SCRIPT=%SCRIPT_DIR%rendering\render_samples.py"

if not exist "%RENDER_SCRIPT%" (
    echo ERROR: render script not found: %RENDER_SCRIPT% >&2
    exit /b 1
)

cd /d "%REPO_ROOT%"

if "%~1"=="" (
    python "%RENDER_SCRIPT%"
) else (
    python "%RENDER_SCRIPT%" %*
)

exit /b %ERRORLEVEL%
