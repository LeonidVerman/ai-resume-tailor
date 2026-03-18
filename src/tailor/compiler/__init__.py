"""Resume compiler pipeline.

Parses a DOCX master resume into a layout-preserving IR, accepts LLM-tailored
plain text, and renders a fresh DOCX with the original formatting intact.

Public API (used by template_fill.save_doc_from_template):
    compile_resume(template_path, llm_text, output_path)
"""

from tailor.compiler.pipeline import compile_resume

__all__ = ["compile_resume"]
