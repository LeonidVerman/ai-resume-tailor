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
rem   tmp\artefacts\classification\docx\<basename>.json                 classification
rem   tmp\artefacts\classification\pdf\<basename>.json                  classification
rem   tmp\artefacts\classification\llm-input\docx\<basename>_input.json LLM input
rem   tmp\artefacts\classification\llm-input\pdf\<basename>_input.json  LLM input

setlocal enabledelayedexpansion

if "%API_URL%"=="" set API_URL=http://localhost:8000

set TESTS_DIR=%~dp0
for %%i in ("%TESTS_DIR%..") do set REPO_ROOT=%%~fi

set DOCX_INPUT=%TESTS_DIR%samples\resume\docx
set PDF_INPUT=%TESTS_DIR%samples\resume\pfd
set DOCX_OUTPUT=%REPO_ROOT%\tmp\artefacts\classification\docx
set PDF_OUTPUT=%REPO_ROOT%\tmp\artefacts\classification\pdf
set DOCX_INPUT_OUTPUT=%REPO_ROOT%\tmp\artefacts\classification\llm-input\docx
set PDF_INPUT_OUTPUT=%REPO_ROOT%\tmp\artefacts\classification\llm-input\pdf

if not exist "%DOCX_OUTPUT%" mkdir "%DOCX_OUTPUT%"
if not exist "%PDF_OUTPUT%" mkdir "%PDF_OUTPUT%"
if not exist "%DOCX_INPUT_OUTPUT%" mkdir "%DOCX_INPUT_OUTPUT%"
if not exist "%PDF_INPUT_OUTPUT%" mkdir "%PDF_INPUT_OUTPUT%"

set CLI_SECRET_HEADER=
if not "%CLI_SECRET%"=="" set CLI_SECRET_HEADER=-H "X-Cli-Secret: %CLI_SECRET%"

echo === Classifying DOCX samples ===
set FOUND_DOCX=0
for %%f in ("%DOCX_INPUT%\*.docx") do (
  set FOUND_DOCX=1
  set TMP_FILE=%TEMP%\classify_tmp_%%~nf.json
  <nul set /p "=  %%~nxf -> "
  curl -s -o "!TMP_FILE!" -w "HTTP %%{http_code}" ^
    -X POST ^
    %CLI_SECRET_HEADER% ^
    -F "file=@%%f" ^
    "%API_URL%/api/v1/admin/classification/classify-file"
  python "%TESTS_DIR%classify_helper.py" "!TMP_FILE!" "%DOCX_OUTPUT%\%%~nf.json" "%DOCX_INPUT_OUTPUT%\%%~nf_input.json"
  del "!TMP_FILE!" 2>nul
  echo.
)
if "%FOUND_DOCX%"=="0" echo   No .docx files found in %DOCX_INPUT%

echo.
echo === Classifying PDF samples ===
set FOUND_PDF=0
for %%f in ("%PDF_INPUT%\*.pdf") do (
  set FOUND_PDF=1
  set TMP_FILE=%TEMP%\classify_tmp_%%~nf.json
  <nul set /p "=  %%~nxf -> "
  curl -s -o "!TMP_FILE!" -w "HTTP %%{http_code}" ^
    -X POST ^
    %CLI_SECRET_HEADER% ^
    -F "file=@%%f" ^
    "%API_URL%/api/v1/admin/classification/classify-file"
  python "%TESTS_DIR%classify_helper.py" "!TMP_FILE!" "%PDF_OUTPUT%\%%~nf.json" "%PDF_INPUT_OUTPUT%\%%~nf_input.json"
  del "!TMP_FILE!" 2>nul
  echo.
)
if "%FOUND_PDF%"=="0" echo   No .pdf files found in %PDF_INPUT%

echo.
echo Done.
echo   Classification: %REPO_ROOT%\tmp\artefacts\classification\{docx,pdf}\
echo   LLM input:      %REPO_ROOT%\tmp\artefacts\classification\llm-input\{docx,pdf}\
