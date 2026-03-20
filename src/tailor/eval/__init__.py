"""PDF round-trip evaluator for the resume pipeline.

Compares source PDFs against pipeline output PDFs (PDF→IR→DOCX→PDF)
at text, structural, and geometric levels.

Usage
-----
    python -m tailor.eval --dir tests/samples/resume/pfd
    python -m tailor.eval --files a.pdf b.pdf
    python -m tailor.eval promote --run-id 2026-03-19_2200
"""
