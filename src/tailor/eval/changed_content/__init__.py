"""Changed-content DOCX layout evaluation framework.

Evaluates how well the compiler pipeline preserves visual layout when
LLM-generated content differs meaningfully from the source template.

Entry points
------------
- BenchmarkCase / CaseResult / run_case() / run_suite()  — benchmark.py
- score_layout() / LayoutScore                            — scorer.py
- classify_failures() / FailureClassification             — taxonomy.py
- build_case_report() / build_suite_report()              — report.py

CLI
---
    python -m tailor.eval.changed_content run --suite SUITE.json
    python -m tailor.eval.changed_content run --case CASE.json
"""
