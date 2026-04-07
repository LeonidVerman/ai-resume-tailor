@echo off
:: Run changed-content postprocessing regression tests.
:: PDFs are rendered via Docker (LibreOffice) for high-fidelity layout scoring.
:: Artefacts land in tmp\artefacts\postprocessing\<case_id>\ for visual inspection.
:: Can be invoked from any directory.

set CC_REGRESSION_PDF_METHOD=docker
python -m pytest "%~dp0test_changed_content_regression.py" -k "veeva" -v --tb=short %*
