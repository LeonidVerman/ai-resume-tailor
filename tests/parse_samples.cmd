@echo off
rem parse_samples.cmd
rem
rem Run the DOCX/PDF parser locally and save the LLM-input JSON to disk.
rem No backend server or LLM call required.
rem
rem Usage:
rem   parse_samples.cmd                          -- parse all samples
rem   parse_samples.cmd samples\resume\docx\10-Template5.docx  -- single file
rem
rem Output:
rem   tmp\artefacts\classification\llm-input\docx\<basename>_input.json
rem   tmp\artefacts\classification\llm-input\pdf\<basename>_input.json

setlocal enabledelayedexpansion

set TESTS_DIR=%~dp0
for %%i in ("%TESTS_DIR%..") do set REPO_ROOT=%%~fi
set SCRIPT=%TESTS_DIR%parse_helper.py
set DOCX_INPUT=%TESTS_DIR%samples\resume\docx
set PDF_INPUT=%TESTS_DIR%samples\resume\pfd
set DOCX_OUT=%REPO_ROOT%\tmp\artefacts\classification\llm-input\docx
set PDF_OUT=%REPO_ROOT%\tmp\artefacts\classification\llm-input\pdf

if not exist "%DOCX_OUT%" mkdir "%DOCX_OUT%"
if not exist "%PDF_OUT%" mkdir "%PDF_OUT%"

if not "%~1"=="" (
  set INPUT=%TESTS_DIR%%~1
  set EXT=%~x1
  if /i "!EXT!"==".docx" (set OUT_DIR=%DOCX_OUT%) else (set OUT_DIR=%PDF_OUT%)
  set BASENAME=%~n1
  echo Parsing: !INPUT!
  set PYTHONPATH=%REPO_ROOT%\src
  python "!SCRIPT!" "!INPUT!" "!OUT_DIR!\!BASENAME!_input.json"
  echo Done.  LLM input: !OUT_DIR!\!BASENAME!_input.json
  goto :eof
)

echo === Parsing DOCX samples ===
set FOUND=0
for %%f in ("%DOCX_INPUT%\*.docx") do (
  set FOUND=1
  <nul set /p "=  %%~nxf -> "
  set PYTHONPATH=%REPO_ROOT%\src
  python "!SCRIPT!" "%%f" "%DOCX_OUT%\%%~nf_input.json" && echo OK || echo FAIL
)
if "%FOUND%"=="0" echo   No .docx files found in %DOCX_INPUT%

echo.
echo === Parsing PDF samples ===
set FOUND=0
for %%f in ("%PDF_INPUT%\*.pdf") do (
  set FOUND=1
  <nul set /p "=  %%~nxf -> "
  set PYTHONPATH=%REPO_ROOT%\src
  python "!SCRIPT!" "%%f" "%PDF_OUT%\%%~nf_input.json" && echo OK || echo FAIL
)
if "%FOUND%"=="0" echo   No .pdf files found in %PDF_INPUT%

echo.
echo Done.  LLM input: %REPO_ROOT%\tmp\artefacts\classification\llm-input\
