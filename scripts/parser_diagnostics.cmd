@echo off
rem parser_diagnostics.cmd
rem
rem Run parser diagnostics against llm-input JSON artefacts.
rem Requires artefacts to exist — run parse_samples.cmd first.
rem
rem Usage:
rem   parser_diagnostics.cmd                     -- scan default dirs
rem   parser_diagnostics.cmd --input path\to\dir -- custom input dir
rem   parser_diagnostics.cmd --help              -- show all options

setlocal
set REPO_ROOT=%~dp0..
set PYTHONPATH=%REPO_ROOT%\src
cd /d "%REPO_ROOT%"
python scripts\parser_diagnostics.py %*
endlocal
