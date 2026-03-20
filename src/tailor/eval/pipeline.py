"""Run the resume pipeline (PDF→IR→DOCX→PDF) without LLM modification.

Reuses the same pipeline as test_format_roundtrip.py: parse_pdf,
identity-serialize via _doc_to_llm_text, compile_resume_from_ir, docx_to_pdf.
"""
from __future__ import annotations

import os
import re
import tempfile
import shutil
from pathlib import Path

_SEMANTIC_CANONICAL_HEADING: dict[str, str] = {
    "summary": "Professional Summary",
    "experience": "Experience",
    "skills": "Technical Skills",
    "education": "Education",
}


def _doc_to_llm_text(doc) -> str:
    """Serialize a ResumeDocument to the text format compile_resume_from_ir expects.

    Identity pass: no LLM, content is preserved verbatim.
    Copied (and kept in sync) from tests/test_format_roundtrip.py.
    """
    from tailor.compiler.models import TableBlock

    has_table_blocks = (
        doc.body_items is not None
        and any(isinstance(i, TableBlock) for i in doc.body_items)
    )

    lines: list[str] = []
    for section in doc.sections:
        if section.semantic_type == "other":
            continue

        _title_parts = re.split(r"[\s\xa0]+", section.title.strip())
        _is_letter_spaced = bool(_title_parts) and all(len(p) <= 1 for p in _title_parts if p)
        if _is_letter_spaced:
            heading_line = _SEMANTIC_CANONICAL_HEADING.get(section.semantic_type, section.title)
        else:
            heading_line = section.title
        lines.append(heading_line)

        if section.semantic_type == "experience":
            if section.roles:
                for role in section.roles:
                    if role.header_extra and "|" not in role.header.text:
                        # PDF separate-line format: combine role title + company
                        # (header_extra) into a single pipe-separated string so
                        # parse_llm_output detects the role via _is_role_header.
                        _hdr_parts = [role.header.text.strip()] + [
                            he.text.strip() for he in role.header_extra if he.text.strip()
                        ]
                        lines.append(" | ".join(_hdr_parts))
                    else:
                        lines.append(role.header.text)
                    for m in role.meta_lines:
                        lines.append(m.text)
                    for b in role.bullets:
                        txt = b.text
                        if txt.startswith("- "):
                            txt = txt[2:]
                        lines.append(f"- {txt}")
            elif not has_table_blocks:
                for p in section.body_paras:
                    if p.text.strip():
                        lines.append(f"- {p.text.strip()}")
        else:
            for p in section.body_paras:
                if p.text.strip():
                    lines.append(f"- {p.text.strip()}")
        lines.append("")

    return "\n".join(lines)


def run_pipeline(
    source_pdf_path: str,
    run_dir: str,
    lo_method: str = "subprocess",
) -> tuple[str, str]:
    """Run PDF→IR→DOCX→PDF pipeline and return (docx_path, output_pdf_path).

    Files are written into *run_dir*.  Raises RuntimeError on failure.

    Parameters
    ----------
    source_pdf_path:
        Path to the source PDF.
    run_dir:
        Directory to write intermediate and output files.
    lo_method:
        LibreOffice conversion method: 'subprocess' | 'docker' | 'local'.
    """
    from tailor.compiler.pdf_parser import parse_pdf
    from tailor.compiler.pipeline import compile_resume_from_ir
    from tailor.config import RESUME_TEMPLATE
    from tailor.docx.pdf import docx_to_pdf

    stem = Path(source_pdf_path).stem
    docx_out = os.path.join(run_dir, f"{stem}.docx")
    pdf_out = os.path.join(run_dir, f"{stem}.pdf")

    # Parse source PDF
    pdf_bytes = Path(source_pdf_path).read_bytes()
    orig_ir = parse_pdf(pdf_bytes)

    # Identity-serialize (no LLM)
    llm_text = _doc_to_llm_text(orig_ir)

    # Render to DOCX
    compile_resume_from_ir(
        template_ir=orig_ir,
        llm_text=llm_text,
        output_path=docx_out,
        style_template_path=str(RESUME_TEMPLATE),
    )

    # Convert DOCX → PDF using a temp working dir
    # docx_to_pdf writes the PDF next to the DOCX, so we work in run_dir
    docx_to_pdf(docx_out, method=lo_method)
    generated_pdf = os.path.splitext(docx_out)[0] + ".pdf"
    if not os.path.exists(generated_pdf):
        raise RuntimeError(f"docx_to_pdf did not produce a PDF at {generated_pdf}")

    # Rename if needed to our canonical name
    if generated_pdf != pdf_out:
        shutil.move(generated_pdf, pdf_out)

    return docx_out, pdf_out
