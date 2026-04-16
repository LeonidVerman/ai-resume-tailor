@echo off
rem classify_samples.cmd
rem
rem Run LLM classification against all resume samples and save JSON output.
rem
rem Optional env vars:
rem   CLI_SECRET -- X-Cli-Secret value (only needed when CLASSIFICATION_CLI_SECRET
rem                 is configured on the server; not required in local development)
rem   API_URL    -- defaults to http://localhost:8000
rem
rem Output:
rem   tmp\artefacts\classification\docx\<basename>.json  (from tests\samples\resume\docx\*.docx)
rem   tmp\artefacts\classification\pdf\<basename>.json   (from tests\samples\resume\pfd\*.pdf)

setlocal enabledelayedexpansion

if "%API_URL%"=="" set API_URL=http://localhost:8000

set TESTS_DIR=%~dp0
for %%i in ("%TESTS_DIR%..") do set REPO_ROOT=%%~fi

set DOCX_INPUT=%TESTS_DIR%samples\resume\docx
set PDF_INPUT=%TESTS_DIR%samples\resume\pfd
set DOCX_OUTPUT=%REPO_ROOT%\tmp\artefacts\classification\docx
set PDF_OUTPUT=%REPO_ROOT%\tmp\artefacts\classification\pdf

if not exist "%DOCX_OUTPUT%" mkdir "%DOCX_OUTPUT%"
if not exist "%PDF_OUTPUT%" mkdir "%PDF_OUTPUT%"

set CLI_SECRET_HEADER=
if not "%CLI_SECRET%"=="" set CLI_SECRET_HEADER=-H "X-Cli-Secret: %CLI_SECRET%"

echo === Classifying DOCX samples ===
set FOUND_DOCX=0
for %%f in ("%DOCX_INPUT%\*.docx") do (
  set FOUND_DOCX=1
  set OUT_FILE=%DOCX_OUTPUT%\%%~nf.json
  <nul set /p "=  %%~nxf -> "
  curl -s -o "!OUT_FILE!" -w "HTTP %%{http_code}" ^
    -X POST ^
    %CLI_SECRET_HEADER% ^
    -F "file=@%%f" ^
    "%API_URL%/api/v1/admin/classification/classify-file"
  echo.
)
if "%FOUND_DOCX%"=="0" echo   No .docx files found in %DOCX_INPUT%

echo.
echo === Classifying PDF samples ===
set FOUND_PDF=0
for %%f in ("%PDF_INPUT%\*.pdf") do (
  set FOUND_PDF=1
  set OUT_FILE=%PDF_OUTPUT%\%%~nf.json
  <nul set /p "=  %%~nxf -> "
  curl -s -o "!OUT_FILE!" -w "HTTP %%{http_code}" ^
    -X POST ^
    %CLI_SECRET_HEADER% ^
    -F "file=@%%f" ^
    "%API_URL%/api/v1/admin/classification/classify-file"
  echo.
)
if "%FOUND_PDF%"=="0" echo   No .pdf files found in %PDF_INPUT%

echo.
echo Done. Results saved to %REPO_ROOT%\tmp\artefacts\classification\
