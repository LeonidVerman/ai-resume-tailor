@echo off
:: tests/test_rendering.cmd
::
:: Run the deterministic rendering test for matched (classification + generation) pairs,
:: then automatically grade layout preservation for the same sample(s),
:: then run cross-pipeline text equivalence comparison.
::
:: Usage (from repo root):
::   tests\test_rendering.cmd                          :: render + grade + compare all matched samples
::   tests\test_rendering.cmd 1                        :: render + grade + compare sample with numeric prefix 1
::   tests\test_rendering.cmd 2 3 4 14 25              :: render + grade + compare specific samples
::   tests\test_rendering.cmd 1-Leonid                 :: render + grade + compare sample matching filename fragment
::   tests\test_rendering.cmd 35 --timeline-row-table-v2  :: render sample 35 with experimental v2 timeline table
::
:: Multiple numeric prefixes are accepted and passed to render_samples.py.
:: --timeline-row-table-v2 is consumed by render_samples.py only and is not
:: forwarded to grade_layout.py or compare_pipelines.py.
:: To grade without re-rendering, use grade_layout.cmd directly.
::
:: Flags --docx-only, --pdf-only, --no-render suppress the cross-pipeline comparison.

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
set "RENDER_SCRIPT=%SCRIPT_DIR%rendering\render_samples.py"
set "COMPARE_SCRIPT=%SCRIPT_DIR%rendering\compare_pipelines.py"

if not exist "%RENDER_SCRIPT%" (
    echo ERROR: render script not found: %RENDER_SCRIPT% >&2
    exit /b 1
)

cd /d "%REPO_ROOT%"

:: Split args: render_samples.py gets all args; grade/compare get everything
:: except --timeline-row-table-v2 (which they don't recognise).
set "GRADER_ARGS="
set "SKIP_COMPARE=0"
for %%A in (%*) do (
    if /I "%%~A"=="--timeline-row-table-v2" (
        :: consumed by render_samples.py only — do not forward
    ) else (
        set "GRADER_ARGS=!GRADER_ARGS! %%A"
    )
    if /I "%%~A"=="--docx-only" set "SKIP_COMPARE=1"
    if /I "%%~A"=="--pdf-only"  set "SKIP_COMPARE=1"
    if /I "%%~A"=="--no-render" set "SKIP_COMPARE=1"
)

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
if "%GRADER_ARGS%"=="" (
    python tests\grade_layout.py --no-render
) else (
    python tests\grade_layout.py --no-render%GRADER_ARGS%
)

if "%SKIP_COMPARE%"=="0" (
    echo.
    if "%GRADER_ARGS%"=="" (
        python "%COMPARE_SCRIPT%"
    ) else (
        python "%COMPARE_SCRIPT%"%GRADER_ARGS%
    )
)

exit /b %RENDER_EXIT%
