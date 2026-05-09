"""Deterministic layout grading for DOCX template rendering.

Three-layer evaluation:
  1. IR Validation    — structural correctness of the generated IR
  2. DOCX Comparison  — structural fidelity vs. the original template
  3. PDF Layout       — visual outcome via PDF extraction

Public API:
    from tailor.eval.layout_grader.grader import grade_sample, SampleGrade
    from tailor.eval.layout_grader.report import build_aggregate, write_summary_txt
"""
from tailor.eval.layout_grader.grader import SampleGrade, grade_sample
from tailor.eval.layout_grader.report import build_aggregate, write_summary_txt

__all__ = ["SampleGrade", "grade_sample", "build_aggregate", "write_summary_txt"]
