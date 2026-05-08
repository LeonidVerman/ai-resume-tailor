@echo off
:: tests/test_rendering.cmd
::
:: Run the deterministic rendering test for matched (classification + generation) pairs,
:: then automatically grade layout preservation for the same sample(s).
::
:: Usage (from repo root):
::   tests\test_rendering.cmd           :: render + grade all matched samples
::   tests\test_rendering.cmd 1         :: render + grade sample with numeric prefix 1
::   tests\test_rendering.cmd 1-Leonid  :: render + grade sample matching filename fragment
::
:: The optional argument is forwarded to both render_samples.py and grade_layout.py.
:: To grade without re-rendering, use grade_layout.cmd directly.

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

set RENDER_EXIT=%ERRORLEVEL%

echo.
echo ============================================================
echo   grade_layout -- grading rendered artefacts
echo ============================================================
set PYTHONPATH=%REPO_ROOT%\src
if "%~1"=="" (
    python tests\grade_layout.py --no-render
) else (
    python tests\grade_layout.py --no-render %*
)

exit /b %RENDER_EXIT%
