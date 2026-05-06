@echo off
:: tests/grade_layout.cmd
::
:: Grade DOCX layout preservation for all matched samples.
:: Renders missing artefacts via the rendering pipeline, then
:: produces per-sample + aggregate reports.
::
:: Usage:
::   grade_layout.cmd                          -- grade all samples
::   grade_layout.cmd 31                       -- grade sample with prefix 31
::   grade_layout.cmd 1-Leonid                 -- match by filename fragment
::   grade_layout.cmd --baseline path\to\aggregate.json
::   grade_layout.cmd --no-render              -- skip rendering step
::   grade_layout.cmd --pdf-method local       -- use built-in PDF converter
::
:: Output:
::   tmp\artefacts\layout_grading\<stem>_grade.json  per-sample grade
::   tmp\artefacts\layout_grading\aggregate.json     aggregate metrics
::   tmp\artefacts\layout_grading\summary.txt        human-readable summary

setlocal
set REPO_ROOT=%~dp0..
set PYTHONPATH=%REPO_ROOT%\src
cd /d "%REPO_ROOT%"
python tests\grade_layout.py %*
endlocal
