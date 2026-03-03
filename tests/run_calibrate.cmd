@echo off
:: Run calibration mode for ai-resume-tailor
:: Sends the unmodified master resume/cover letter to assessment to establish a baseline.
:: Usage: run_calibrate.cmd [--positions FILE] [--model MODEL] [--temperature T] [--workers N] [--max_positions N] [--out DIR] [--cache_dir DIR]
:: Defaults: positions=tests\data\positions.txt, model=gpt-5.2, temperature=0.5

setlocal

:: Defaults
set POSITIONS=tests\data\positions.txt
set MODEL=gpt-5.2
set TEMPERATURE=0.5
set WORKERS=
set MAX_POSITIONS=
set OUT=reports
set CACHE_DIR=

:: Override defaults with any provided arguments (pass-through)
set EXTRA_ARGS=%*

:: Change to repo root (script lives in tests\, repo root is one level up)
cd /d "%~dp0.."

:: Build the command
set CMD=python -m tailor assess --calibrate --positions "%POSITIONS%" --model "%MODEL%" --temperature %TEMPERATURE% --out "%OUT%"

if not "%WORKERS%"=="" set CMD=%CMD% --workers %WORKERS%
if not "%MAX_POSITIONS%"=="" set CMD=%CMD% --max_positions %MAX_POSITIONS%
if not "%CACHE_DIR%"=="" set CMD=%CMD% --cache_dir "%CACHE_DIR%"

:: Append any extra CLI args passed directly to this script
if not "%EXTRA_ARGS%"=="" (
    set CMD=%CMD% %EXTRA_ARGS%
)

echo Running: %CMD%
echo.

set PYTHONPATH=src
%CMD%

endlocal
