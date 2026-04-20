@echo off
rem generate_classification_report.cmd
rem
rem Generate classification report from existing artefacts.
rem Does NOT re-run classification or call the LLM.
rem
rem Usage:
rem   generate_classification_report.cmd

setlocal

set TESTS_DIR=%~dp0
for %%i in ("%TESTS_DIR%..") do set REPO_ROOT=%%~fi

python "%REPO_ROOT%\scripts\generate_classification_report.py" %*
