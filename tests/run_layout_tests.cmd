@echo off
:: Run format roundtrip tests with artefact saving enabled.
:: Generated DOCX files land in tmp\artefacts\ for visual inspection.
:: Can be invoked from any directory.

set SAVE_ARTEFACTS=1
python -m pytest "%~dp0test_format_roundtrip.py" -v -s %*
