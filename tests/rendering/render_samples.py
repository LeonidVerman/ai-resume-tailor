#!/usr/bin/env python3
"""Deterministic rendering test for matched (classification + generation) pairs.

Discovers matched pairs by numeric prefix, then runs the full webapp rendering
pipeline for each: parse template → apply LLM output → render DOCX → PDF.

Fresh IR is always built from the original DOCX/PDF template — never reused
from the pre-stored updated_ir in generation JSON files.

The pipeline path mirrors what the webapp uses:
  DOCX source:
      compile_resume(template_path, llm_text, output_path)  ← same as RenderingService

  PDF source:
      Skipped (requires LibreOffice PDF→DOCX conversion at runtime; not available
      in all environments).  Re-enable by implementing pdf_to_ir() below.

Discovery:
  classification/docx/{N}-*_input.json  ← DOCX classified templates
  classification/pdf/{N}-*_input.json   ← PDF classified templates (skipped)
  generation/{N}-*.json                 ← LLM tailoring output

Classification is loaded from the "structured_resume" field of the generation
JSON file (populated by generate_run_data).  If the field is absent or empty,
rendering proceeds without classification constraints.

Matching is by numeric prefix N.  If multiple files share the same N within the
same source kind, the script aborts with a disambiguation error.

Usage:
  python tests/rendering/render_samples.py            # all matched pairs
  python tests/rendering/render_samples.py 1          # sample with prefix 1
  python tests/rendering/render_samples.py 1-Leonid   # match by filename fragment
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[2]
_TESTS = _REPO / "tests"
_SAMPLES = _TESTS / "samples"

_GEN_DIR      = _SAMPLES / "generation"
_RES_DOCX_DIR = _SAMPLES / "resume" / "docx"
_RES_PDF_DIR  = _SAMPLES / "resume" / "pfd"   # note: legacy typo preserved

_OUT_IR_DOCX  = _REPO / "tmp" / "artefacts" / "ir"      / "docx"
_OUT_IR_PDF   = _REPO / "tmp" / "artefacts" / "ir"      / "pdf"
_OUT_REND_DOCX = _REPO / "tmp" / "artefacts" / "rendering" / "docx"
_OUT_REND_PDF  = _REPO / "tmp" / "artefacts" / "rendering" / "pdf"
# Staging dir: intermediate DOCX that LibreOffice converts.  Kept separate from
# _OUT_REND_PDF so that LibreOffice's output PDF lands in a fresh location and
# the subsequent shutil.copy2 to _OUT_REND_PDF is never a same-file copy
# (which causes PermissionError [WinError 32] on Windows).
_OUT_STAGE_PDF = _OUT_REND_PDF / "_stage"

_OUT_SS_DOCX = _OUT_REND_DOCX / "screenshots"
_OUT_SS_PDF  = _OUT_REND_PDF  / "screenshots"

_NUM_RE = re.compile(r"^(\d+)-")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SamplePair:
    prefix: str
    source_kind: str           # "docx" or "pdf"
    resume_path: Path          # actual .docx / .pdf template file
    gen_path: Path             # generation JSON


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _num_prefix(name: str) -> Optional[str]:
    m = _NUM_RE.match(name)
    return m.group(1) if m else None


def discover(filter_arg: Optional[str] = None) -> tuple[list[SamplePair], list[str]]:
    """Return (matched_pairs, skip_messages).

    Pairs a generation JSON with the resume DOCX/PDF that shares its numeric prefix.
    Each prefix yields up to two pairs: one DOCX-origin and one PDF-origin.
    Classification is read from the generation JSON's ``structured_resume`` field
    at render time — no external classification files are required.

    *filter_arg* narrows to one or more numeric prefixes or a filename fragment.
    Raises ValueError on ambiguous multi-file conflicts.
    """
    # Index generation files by numeric prefix
    gen_index: dict[str, list[Path]] = {}
    for p in _GEN_DIR.glob("*.json"):
        n = _num_prefix(p.name)
        if n:
            gen_index.setdefault(n, []).append(p)

    # Index DOCX resume files by numeric prefix
    res_docx_index: dict[str, list[Path]] = {}
    for p in _RES_DOCX_DIR.glob("*.docx"):
        n = _num_prefix(p.name)
        if n:
            res_docx_index.setdefault(n, []).append(p)

    # Index PDF resume files by numeric prefix
    res_pdf_index: dict[str, list[Path]] = {}
    for p in _RES_PDF_DIR.glob("*.pdf"):
        n = _num_prefix(p.name)
        if n:
            res_pdf_index.setdefault(n, []).append(p)

    # Check for ambiguous multi-file conflicts
    for n, paths in gen_index.items():
        if len(paths) > 1:
            raise ValueError(
                f"Ambiguous: prefix {n!r} has {len(paths)} generation files: "
                + ", ".join(p.name for p in paths)
            )
    for idx_name, idx in [("DOCX", res_docx_index), ("PDF", res_pdf_index)]:
        for n, paths in idx.items():
            if len(paths) > 1:
                raise ValueError(
                    f"Ambiguous: prefix {n!r} has {len(paths)} {idx_name} resume files: "
                    + ", ".join(p.name for p in paths)
                )

    pairs: list[SamplePair] = []
    skips: list[str] = []

    for n in sorted(gen_index.keys(), key=int):
        gen_path = gen_index[n][0]
        if n in res_docx_index:
            pairs.append(SamplePair(
                prefix=n,
                source_kind="docx",
                resume_path=res_docx_index[n][0],
                gen_path=gen_path,
            ))
        else:
            skips.append(f"[{n}] skip - no resume DOCX found for prefix {n}")
        if n in res_pdf_index:
            pairs.append(SamplePair(
                prefix=n,
                source_kind="pdf",
                resume_path=res_pdf_index[n][0],
                gen_path=gen_path,
            ))

    # Filter by user argument(s).  Multiple numeric prefixes may be passed
    # (e.g. "2 3 4 14 25") and are treated as an OR filter.
    if filter_arg:
        _tokens = [t.strip() for t in filter_arg.replace(",", " ").split() if t.strip()]
        _num_matches: set[str] = set()
        _fragments: list[str] = []
        for tok in _tokens:
            nm = _num_prefix(tok + "-dummy")
            if nm is None:
                nm = tok.split("-")[0] if tok and tok[0].isdigit() else None
            if nm:
                _num_matches.add(nm)
            else:
                _fragments.append(tok.lower())

        if _num_matches:
            pairs = [p for p in pairs if p.prefix in _num_matches]
            skips = [s for s in skips if any(f"[{nm}]" in s for nm in _num_matches)]
        elif _fragments:
            pairs = [p for p in pairs
                     if any(frag in p.gen_path.name.lower()
                            or frag in p.resume_path.name.lower()
                            for frag in _fragments)]
            skips = []

    return pairs, skips


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

def _generate_screenshot(
    original_path: Path,
    rendered_path: Path,
    out_path: Path,
    zoom: float = 2.0,
    verbose: bool = True,
    tag: str = "",
) -> None:
    """Generate a side-by-side comparison screenshot (non-fatal on failure).

    Always deletes the existing PNG first so a stale screenshot never survives
    a failed regeneration — if generation fails, the file is absent rather than
    showing outdated content.
    """
    # Remove old file unconditionally so a failed regeneration leaves no stale PNG.
    if out_path.exists():
        out_path.unlink()
    try:
        sys.path.insert(0, str(_REPO / "scripts"))
        from render_screenshot import make_comparison
        make_comparison(
            original_path=original_path,
            rendered_path=rendered_path,
            out_path=out_path,
            zoom=zoom,
        )
        if verbose:
            print(f"  SS    -> {out_path.relative_to(_REPO)}")
    except Exception as exc:
        print(f"{tag} WARN [stage=screenshot] {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_sample(pair: SamplePair, verbose: bool = True, screenshots: bool = True,
                  screenshot_zoom: float = 2.0) -> bool:
    """Run the full pipeline for one sample.  Returns True on success."""
    tag = f"[{pair.prefix}/{pair.source_kind.upper()}]"

    if verbose:
        print(f"\n{tag} -- rendering ------------------------------------------")
        print(f"  resume: {pair.resume_path.relative_to(_REPO)}")
        print(f"  gen:    {pair.gen_path.relative_to(_REPO)}")

    # ── Stage 1: load LLM text and structured_resume ──────────────────────
    structured_resume_data = None
    try:
        with open(pair.gen_path, encoding="utf-8") as f:
            gen = json.load(f)
        llm_text: str = gen["llm_response"]["resume"]
        if not llm_text or not llm_text.strip():
            print(f"{tag} SKIP — empty LLM resume text in generation file")
            return False
        structured_resume_data = gen.get("structured_resume") or None
    except Exception as e:
        print(f"{tag} FAIL [stage=load-llm] {type(e).__name__}: {e}")
        return False

    if verbose:
        print(f"  llm_text length: {len(llm_text)} chars")

    # ── Stage 2: set up output paths ───────────────────────────────────────
    stem = pair.resume_path.stem   # e.g. "1-Leonid_Verman_Resume_Template"

    if pair.source_kind == "docx":
        out_ir_dir   = _OUT_IR_DOCX
        out_rend_dir = _OUT_REND_DOCX
    else:
        out_ir_dir   = _OUT_IR_PDF
        # Use a staging sub-dir so LibreOffice writes its PDF next to a fresh
        # DOCX (no pre-existing same-named PDF), and the later shutil.copy2 to
        # _OUT_REND_PDF is always a genuine different-file copy.
        out_rend_dir = _OUT_STAGE_PDF

    out_ir_dir.mkdir(parents=True, exist_ok=True)
    out_rend_dir.mkdir(parents=True, exist_ok=True)

    out_ir   = out_ir_dir   / f"{stem}_IR.json"
    out_docx = out_rend_dir / f"{stem}.docx"
    out_pdf  = out_rend_dir / f"{stem}.pdf"

    # ── Stage 3: load classification from structured_resume in debug data ────
    classification = None
    if structured_resume_data:
        try:
            sys.path.insert(0, str(_REPO / "src"))
            from tailor.compiler.classification_models import ClassificationOutput
            cls_dict = structured_resume_data.get("classification")
            if cls_dict:
                classification = ClassificationOutput.from_dict(cls_dict)
                if verbose:
                    print(f"  classification: loaded from structured_resume in debug data")
        except Exception as e:
            print(f"{tag} WARN [stage=load-classification] {type(e).__name__}: {e} — rendering without classification")
            classification = None

    # ── Stage 4: run the webapp rendering pipeline ─────────────────────────
    try:
        sys.path.insert(0, str(_REPO / "src"))

        if pair.source_kind == "docx":
            from tailor.compiler.pipeline import compile_resume
            updated = compile_resume(
                template_path=str(pair.resume_path),
                llm_text=llm_text,
                output_path=str(out_docx),
                classification=classification,
            )
        else:  # pdf
            from tailor.compiler.pipeline import compile_resume_from_pdf
            style_docx = _RES_DOCX_DIR / (stem + ".docx")
            if not style_docx.exists():
                print(f"{tag} SKIP — no companion DOCX style template for {stem!r}")
                return False
            updated = compile_resume_from_pdf(
                pdf_path=str(pair.resume_path),
                llm_text=llm_text,
                output_path=str(out_docx),
                style_template_path=str(style_docx),
                classification=classification,
            )
    except Exception as e:
        print(f"{tag} FAIL [stage=compile-resume] {type(e).__name__}: {e}")
        if verbose:
            traceback.print_exc()
        return False

    if verbose:
        print(f"  DOCX  -> {out_docx.relative_to(_REPO)}")

    # ── Stage 5: save fresh IR ─────────────────────────────────────────────
    try:
        with open(out_ir, "w", encoding="utf-8") as f:
            json.dump(updated.to_dict(), f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"{tag} FAIL [stage=save-ir] {type(e).__name__}: {e}")
        return False

    if verbose:
        print(f"  IR    -> {out_ir.relative_to(_REPO)}")

    # ── Stage 5: DOCX → PDF via LibreOffice ───────────────────────────────
    try:
        import gc
        gc.collect()  # release any lingering python-docx / lxml handles before LibreOffice

        from tailor.docx.pdf import _docx_to_pdf_subprocess

        _docx_to_pdf_subprocess(str(out_docx))
        lo_pdf = out_docx.with_suffix(".pdf")
        grader_pdf = _OUT_REND_PDF / lo_pdf.name
        if lo_pdf.exists():
            # The grader reads PDFs from _OUT_REND_PDF.  Always copy the freshly
            # rendered PDF there so the grader uses the latest version.
            # For PDF-path, out_docx is in _OUT_STAGE_PDF so lo_pdf != grader_pdf
            # and shutil.copy2 is a genuine different-file copy (no WinError 32).
            _OUT_REND_PDF.mkdir(parents=True, exist_ok=True)
            if lo_pdf.resolve() != grader_pdf.resolve():
                shutil.copy2(str(lo_pdf), str(grader_pdf))
            # Keep the docx-dir / staging copy in place (used by test_sparse_page_detection)
        elif grader_pdf.exists():
            # lo_pdf wasn't created by LibreOffice but the grader already has a copy.
            if verbose:
                print(f"{tag} WARN: PDF not refreshed — LibreOffice did not produce {lo_pdf.name}")
        else:
            raise FileNotFoundError(f"PDF not found at {grader_pdf}")
    except Exception as e:
        print(f"{tag} FAIL [stage=docx-to-pdf] {type(e).__name__}: {e}")
        if verbose:
            traceback.print_exc()
        return False

    if verbose:
        print(f"  PDF   -> {grader_pdf.relative_to(_REPO)}")

    # ── Stage 6: side-by-side screenshot ──────────────────────────────────
    if screenshots:
        if pair.source_kind == "docx":
            ss_dir = _OUT_SS_DOCX
            # Compare original DOCX template → rendered PDF (best visual fidelity)
            _generate_screenshot(
                original_path=pair.resume_path,
                rendered_path=grader_pdf,
                out_path=ss_dir / f"{stem}.png",
                zoom=screenshot_zoom,
                verbose=verbose,
                tag=tag,
            )
        else:
            ss_dir = _OUT_SS_PDF
            _generate_screenshot(
                original_path=pair.resume_path,
                rendered_path=grader_pdf,
                out_path=ss_dir / f"{stem}.png",
                zoom=screenshot_zoom,
                verbose=verbose,
                tag=tag,
            )

    if verbose:
        print(f"{tag} OK")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Deterministic rendering test for matched samples.",
        add_help=False,
    )
    parser.add_argument("filter", nargs="*", default=None,
                        help="Numeric prefix(es) or filename fragment to filter samples. "
                             "Multiple values are accepted: render_samples.py 2 3 4 14")
    parser.add_argument("--screenshots", dest="screenshots", action="store_true",
                        default=True, help="Generate comparison screenshots (default).")
    parser.add_argument("--no-screenshots", dest="screenshots", action="store_false",
                        help="Disable screenshot generation.")
    parser.add_argument("--strict-screenshots", action="store_true", default=False,
                        help="Fail the run if any screenshot fails (default: warnings only).")
    parser.add_argument("--screenshot-zoom", type=float, default=2.0, metavar="ZOOM",
                        help="PyMuPDF rendering zoom factor (default 2.0).")
    parser.add_argument("-h", "--help", action="help",
                        help="Show this help message and exit.")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    # Join multiple positional args: "2 3 4" → "2 3 4" so discover() can split them.
    filter_arg = " ".join(args.filter) if args.filter else None
    screenshots = args.screenshots
    zoom = args.screenshot_zoom

    print("=" * 60)
    print("  render_samples.py - deterministic rendering test")
    print("=" * 60)
    if screenshots:
        print(f"  Screenshots: ON (zoom={zoom}x)")
    else:
        print("  Screenshots: OFF")

    try:
        pairs, skips = discover(filter_arg)
    except ValueError as e:
        print(f"\nDISCOVERY ERROR: {e}")
        return 1

    if not pairs and not skips:
        print("\nNo samples matched the filter.")
        return 1

    if skips:
        print(f"\nSkipped ({len(skips)}):")
        for s in skips:
            print(f"  {s}")

    if not pairs:
        print("\nNo matched pairs to render.")
        return 0

    print(f"\nMatched pairs ({len(pairs)}):")
    for p in pairs:
        print(f"  [{p.prefix}/{p.source_kind.upper()}] {p.resume_path.name}")

    n_ok = n_fail = 0
    for pair in pairs:
        if render_sample(pair, verbose=True, screenshots=screenshots,
                         screenshot_zoom=zoom):
            n_ok += 1
        else:
            n_fail += 1

    print("\n" + "=" * 60)
    print(f"  Results: {n_ok} OK  |  {n_fail} FAILED  |  {len(skips)} skipped")
    print("=" * 60)

    if n_fail:
        print("\nOutput artifacts (where generated):")
        for d in (_OUT_IR_DOCX, _OUT_IR_PDF, _OUT_REND_DOCX, _OUT_REND_PDF, _OUT_STAGE_PDF):
            if d.exists():
                files = sorted(d.iterdir())
                if files:
                    print(f"  {d.relative_to(_REPO)}/  ({len(files)} files)")

    if screenshots:
        print("\nScreenshots:")
        for ss_dir in (_OUT_SS_DOCX, _OUT_SS_PDF):
            if ss_dir.exists():
                pngs = sorted(ss_dir.glob("*.png"))
                if pngs:
                    print(f"  {ss_dir.relative_to(_REPO)}/  ({len(pngs)} files)")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
