@echo off
:: Run calibration-with-data mode for ai-resume-tailor
:: Sends pre-generated sample resume/cover letter files to assessment.
:: Usage: run_calibrate_samples.cmd [--positions FILE] [--model MODEL] [--temperature T] [--workers N] [--max_positions N] [--out DIR] [--cache_dir DIR]
:: Defaults: positions=tests\data\positions.txt, calibrate-data=tests\samples, model=gpt-5.2, temperature=0.5

setlocal

:: Defaults
set POSITIONS=tests\data\positions.txt
set CALIBRATE_DATA=tests\samples
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
set CMD=python -m tailor assess --calibrate-data "%CALIBRATE_DATA%" --positions "%POSITIONS%" --model "%MODEL%" --temperature %TEMPERATURE% --out "%OUT%"

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
