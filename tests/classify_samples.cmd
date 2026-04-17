@echo off
rem classify_samples.cmd
rem
rem Run LLM classification against resume samples and save JSON output.
rem
rem Usage:
rem   classify_samples.cmd                          -- classify all samples
rem   classify_samples.cmd samples\resume\docx\10-Template5.docx  -- single file
rem   classify_samples.cmd samples\resume\pfd\some.pdf             -- single file
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

rem Helper: classify one file and save output.
rem   %1 = full path to input file
rem   %2 = output json path
rem   %3 = llm-input json path
:classify_one
  set _INPUT=%~1
  set _OUT=%~2
  set _IN=%~3
  set _BASENAME=%~n1
  set _TMP=%TEMP%\classify_tmp_!_BASENAME!.json
  set _HTTP_TMP=%TEMP%\classify_http_!_BASENAME!.txt

  curl -s -o "!_TMP!" -w "%%{http_code}" ^
    -X POST ^
    %CLI_SECRET_HEADER% ^
    -F "file=@!_INPUT!" ^
    "%API_URL%/api/v1/admin/classification/classify-file" > "!_HTTP_TMP!" 2>nul
  set /p _HTTP_CODE=<"!_HTTP_TMP!"
  del "!_HTTP_TMP!" 2>nul

  if "!_HTTP_CODE!"=="200" (
    python "%TESTS_DIR%classify_helper.py" "!_TMP!" "!_OUT!" "!_IN!"
    echo OK
  ) else (
    echo FAIL (HTTP !_HTTP_CODE!)
  )
  del "!_TMP!" 2>nul
goto :eof

rem -----------------------------------------------------------------------
rem If a single file argument was provided, classify only that file
rem -----------------------------------------------------------------------
if not "%~1"=="" (
  set SINGLE_FILE=%TESTS_DIR%%~1
  set EXT=%~x1
  if /i "!EXT!"==".docx" (
    set OUT_DIR=%DOCX_OUTPUT%
    set IN_DIR=%DOCX_INPUT_OUTPUT%
  ) else (
    set OUT_DIR=%PDF_OUTPUT%
    set IN_DIR=%PDF_INPUT_OUTPUT%
  )
  set BASENAME=%~n1
  echo Classifying: !SINGLE_FILE!
  call :classify_one "!SINGLE_FILE!" "!OUT_DIR!\!BASENAME!.json" "!IN_DIR!\!BASENAME!_input.json"
  echo.
  echo Done.
  echo   Classification: !OUT_DIR!\!BASENAME!.json
  echo   LLM input:      !IN_DIR!\!BASENAME!_input.json
  goto :eof
)

rem -----------------------------------------------------------------------
rem No argument — classify all DOCX then all PDF samples
rem -----------------------------------------------------------------------
echo === Classifying DOCX samples ===
set FOUND_DOCX=0
for %%f in ("%DOCX_INPUT%\*.docx") do (
  set FOUND_DOCX=1
  set _BNAME=%%~nf
  <nul set /p "=  %%~nxf -> "
  call :classify_one "%%f" "%DOCX_OUTPUT%\%%~nf.json" "%DOCX_INPUT_OUTPUT%\%%~nf_input.json"
)
if "%FOUND_DOCX%"=="0" echo   No .docx files found in %DOCX_INPUT%

echo.
echo === Classifying PDF samples ===
set FOUND_PDF=0
for %%f in ("%PDF_INPUT%\*.pdf") do (
  set FOUND_PDF=1
  <nul set /p "=  %%~nxf -> "
  call :classify_one "%%f" "%PDF_OUTPUT%\%%~nf.json" "%PDF_INPUT_OUTPUT%\%%~nf_input.json"
)
if "%FOUND_PDF%"=="0" echo   No .pdf files found in %PDF_INPUT%

echo.
echo Done.
echo   Classification: %REPO_ROOT%\tmp\artefacts\classification\{docx,pdf}\
echo   LLM input:      %REPO_ROOT%\tmp\artefacts\classification\llm-input\{docx,pdf}\
