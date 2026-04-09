@echo off
:: scripts\run_tests.cmd
::
:: Run the full test suite.
::
:: Usage:
::   scripts\run_tests.cmd              :: run all tests
::   scripts\run_tests.cmd backend      :: run backend tests only
::   scripts\run_tests.cmd cli          :: run CLI generator tests only
::   scripts\run_tests.cmd fast         :: run fast tests (skip slow)

setlocal

:: Resolve repo root
set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
pushd "%REPO_ROOT%"
set "REPO_ROOT=%CD%"
popd

cd /d "%REPO_ROOT%"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=all"

if /i "%TARGET%"=="backend" (
    echo [run_tests] Running backend tests...
    cd /d "%REPO_ROOT%\backend" && python -m pytest tests/ -v
    goto done
)

if /i "%TARGET%"=="cli" (
    echo [run_tests] Running CLI generator tests...
    python -m pytest tests/ -v
    goto done
)

if /i "%TARGET%"=="fast" (
    echo [run_tests] Running fast tests (no slow markers^)...
    python -m pytest tests/ -v -m "not slow"
    goto done
)

echo [run_tests] Running all tests...
python -m pytest tests/ -v

:done
echo [run_tests] Done.
