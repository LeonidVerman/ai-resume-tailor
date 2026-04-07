"""Deterministic changed-content regression tests.

Validates the post-processing / renderer pipeline using stored template DOCX
+ debug JSON pairs.  No LLM calls are made: the generated resume text is read
directly from the debug JSON artifact.

Adding a new regression case
-----------------------------
1. Drop the template DOCX into  tests/samples/resume/docx/
2. Drop the debug JSON into     tests/samples/generation/
3. Add one entry to            tests/regression_cases.json

No code changes are required — tests pick up new cases automatically.

PDF rendering
-------------
Tests default to ``method='local'`` (built-in xhtml2pdf) so they run
everywhere without external dependencies.  Set the environment variable
``CC_REGRESSION_PDF_METHOD=subprocess`` to use a locally installed
LibreOffice; this produces higher-fidelity layout scores at the cost of
requiring LibreOffice in PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TESTS_DIR    = Path(__file__).parent
_MANIFEST     = _TESTS_DIR / "regression_cases.json"
_REPO_ROOT    = _TESTS_DIR.parent


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    """Load and return the parsed regression_cases.json manifest.

    Raises FileNotFoundError if the manifest is missing.
    Raises ValueError if the JSON is malformed or 'cases' is absent.
    """
    if not _MANIFEST.exists():
        raise FileNotFoundError(f"Regression manifest not found: {_MANIFEST}")
    raw = _MANIFEST.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed regression manifest: {exc}") from exc
    if "cases" not in data:
        raise ValueError("Regression manifest missing required 'cases' key")
    return data


def _resolve_path(p: str) -> Path:
    """Resolve a path relative to the repository root."""
    result = _REPO_ROOT / p
    return result


def _effective_thresholds(case: dict, defaults: dict) -> dict:
    """Merge per-case threshold overrides with global defaults.

    Per-case 'thresholds' take priority; anything absent falls back to defaults.
    """
    merged = dict(defaults)
    merged.update(case.get("thresholds", {}))
    return merged


# ---------------------------------------------------------------------------
# Debug JSON helpers
# ---------------------------------------------------------------------------

def load_resume_text(debug_json_path: str | Path) -> str:
    """Extract the generated resume text from a debug JSON artifact.

    Expected structure::

        {
          "llm_response": {
            "resume": "<plain-text resume>"
          }
        }

    Raises
    ------
    FileNotFoundError
        If the debug JSON file does not exist.
    KeyError
        If ``llm_response`` or ``resume`` is missing from the JSON.
    ValueError
        If the JSON cannot be parsed or ``resume`` is empty.
    """
    path = Path(debug_json_path)
    if not path.exists():
        raise FileNotFoundError(f"Debug JSON not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cannot parse debug JSON {path}: {exc}") from exc

    if "llm_response" not in data:
        raise KeyError(
            f"Debug JSON missing 'llm_response' key: {path}\n"
            f"Available top-level keys: {list(data.keys())}"
        )

    llm_response = data["llm_response"]
    if not isinstance(llm_response, dict):
        raise ValueError(
            f"'llm_response' must be a dict, got {type(llm_response).__name__}: {path}"
        )

    if "resume" not in llm_response:
        raise KeyError(
            f"Debug JSON 'llm_response' missing 'resume' key: {path}\n"
            f"Available keys: {list(llm_response.keys())}"
        )

    resume_text = llm_response["resume"]
    if not isinstance(resume_text, str) or not resume_text.strip():
        raise ValueError(
            f"'llm_response.resume' is empty or not a string: {path}"
        )

    return resume_text


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def _detect_pdf_method() -> str:
    """Return the best available PDF conversion method.

    Priority:
      1. ``CC_REGRESSION_PDF_METHOD`` env var (explicit override)
      2. ``subprocess`` if LibreOffice is in PATH
      3. ``local`` (built-in xhtml2pdf, always available)
    """
    env_method = os.environ.get("CC_REGRESSION_PDF_METHOD", "").strip().lower()
    if env_method in ("subprocess", "docker", "local"):
        return env_method

    if shutil.which("soffice") or shutil.which("libreoffice"):
        return "subprocess"

    return "local"


def _convert_docx_to_pdf(docx_path: str, dest_pdf: str, method: str) -> None:
    """Convert *docx_path* to PDF at *dest_pdf*.

    Works directory-safely by copying the DOCX alongside the target PDF path
    first (mirrors benchmark.py _convert_docx_to_pdf).
    """
    from tailor.docx.pdf import docx_to_pdf

    work_dir  = os.path.dirname(dest_pdf)
    stem      = Path(dest_pdf).stem
    work_docx = os.path.join(work_dir, stem + ".docx")

    shutil.copy2(docx_path, work_docx)
    try:
        docx_to_pdf(work_docx, method=method)
        generated = os.path.splitext(work_docx)[0] + ".pdf"
        if not os.path.exists(generated):
            raise RuntimeError(
                f"docx_to_pdf did not produce a PDF at {generated}"
            )
        if generated != dest_pdf:
            shutil.move(generated, dest_pdf)
    finally:
        if os.path.exists(work_docx) and work_docx != docx_path:
            os.remove(work_docx)


class RegressionResult:
    """Structured result from one regression run."""

    def __init__(
        self,
        case_id: str,
        template_path: str,
        pdf_method: str,
        score,                     # LayoutScore dataclass
        evidence: list[str],
        failure_classes: list[str],
        output_dir: str,
    ) -> None:
        self.case_id        = case_id
        self.template_path  = template_path
        self.pdf_method     = pdf_method
        self.score          = score
        self.evidence       = evidence
        self.failure_classes = failure_classes
        self.output_dir     = output_dir

    # Convenience shorthands
    @property
    def composite(self) -> float:
        return self.score.composite

    @property
    def topology(self) -> float:
        return self.score.topology_preservation

    @property
    def placement(self) -> float:
        return self.score.section_placement

    @property
    def coherence(self) -> float:
        return self.score.overall_section_coherence

    @property
    def high_stress_count(self) -> int:
        return sum(
            1 for r in (self.score.section_stress_results or [])
            if (r.get("stress_level") if isinstance(r, dict) else r.stress_level) == "high"
        )

    def diagnostic(self) -> str:
        """Return a human-readable diagnostic string for assertion messages."""
        lines = [
            f"case_id              : {self.case_id}",
            f"pdf_method           : {self.pdf_method}",
            f"composite            : {self.composite:.3f}",
            f"topology_preservation: {self.topology:.3f}",
            f"section_placement    : {self.placement:.3f}",
            f"section_coherence    : {self.coherence:.3f}",
            f"high_stress_sections : {self.high_stress_count}",
            f"failure_classes      : {self.failure_classes}",
            f"artifacts            : {self.output_dir}",
        ]
        if self.evidence:
            lines.append("evidence (top 5):")
            for ev in self.evidence[:5]:
                lines.append(f"  {ev}")
        return "\n".join(lines)


def run_regression_case(
    case_id: str,
    template_docx: str,
    debug_json: str,
    pdf_method: str,
    output_dir: str,
) -> RegressionResult:
    """Run the full changed-content pipeline for one regression case.

    Steps
    -----
    1. Extract resume text from debug JSON.
    2. Compile: template DOCX + resume text → output DOCX (real pipeline).
    3. Convert both DOCX files to PDF.
    4. Extract layout from both PDFs.
    5. Score and classify failures.

    Parameters
    ----------
    case_id:      Identifier for logging/diagnostics.
    template_docx: Path to the source/template DOCX.
    debug_json:   Path to the debug JSON containing ``llm_response.resume``.
    pdf_method:   ``'local'`` | ``'subprocess'`` | ``'docker'``.
    output_dir:   Directory to write all pipeline artifacts.

    Returns
    -------
    RegressionResult
    """
    from tailor.compiler.pipeline import compile_resume
    from tailor.compiler.docx_parser import parse_docx as _parse_docx
    from tailor.eval.extractor import extract
    from tailor.eval.changed_content.scorer import score_layout
    from tailor.eval.changed_content.taxonomy import classify_failures

    os.makedirs(output_dir, exist_ok=True)

    # ── 1. Extract resume text ───────────────────────────────────────────
    resume_text = load_resume_text(debug_json)

    # ── 2. Compile DOCX ─────────────────────────────────────────────────
    output_docx = os.path.join(output_dir, f"{case_id}_output.docx")
    compile_resume(template_docx, resume_text, output_docx)
    assert os.path.exists(output_docx), f"compile_resume did not produce {output_docx}"

    # ── 3. Convert to PDF ────────────────────────────────────────────────
    src_pdf = os.path.join(output_dir, "source.pdf")
    out_pdf = os.path.join(output_dir, "output.pdf")
    _convert_docx_to_pdf(template_docx, src_pdf, method=pdf_method)
    _convert_docx_to_pdf(output_docx,   out_pdf, method=pdf_method)

    # ── 4. Extract layout ────────────────────────────────────────────────
    src_extracted = extract(src_pdf)
    out_extracted = extract(out_pdf)

    # Source plain text (for duplication detection)
    parsed = _parse_docx(template_docx)
    source_text = "\n".join(p.text for p in parsed.all_paras if p.text.strip())

    # ── 5. Score and classify ────────────────────────────────────────────
    layout_score, evidence = score_layout(
        src_extracted, out_extracted, source_text, resume_text
    )
    fc = classify_failures(layout_score)

    # Write evaluator report alongside artifacts
    from tailor.eval.changed_content.benchmark import CaseResult
    from tailor.eval.changed_content.report import build_case_report, write_case_report

    case_result = CaseResult(
        case_id=case_id,
        template_class="unknown",
        severity="S3",
        layout_score=layout_score.composite,
        metric_breakdown=asdict(layout_score),
        failure_classes=fc.classes,
        evidence=evidence,
        artifacts={"output_dir": output_dir},
    )
    report = build_case_report(case_result)
    write_case_report(report, output_dir)

    return RegressionResult(
        case_id=case_id,
        template_path=template_docx,
        pdf_method=pdf_method,
        score=layout_score,
        evidence=evidence,
        failure_classes=fc.classes,
        output_dir=output_dir,
    )


# ---------------------------------------------------------------------------
# Parametrize from manifest
# ---------------------------------------------------------------------------

def _build_pytest_params() -> list[pytest.param]:
    """Build parametrize list from manifest; skip gracefully on bad manifest."""
    try:
        manifest = _load_manifest()
    except Exception as exc:
        # Return a single failing param so the manifest error surfaces in pytest
        return [pytest.param("__manifest_error__", None, None, {}, marks=pytest.mark.xfail(
            reason=f"Manifest load failed: {exc}", strict=True
        ))]

    defaults = manifest.get("defaults", {})
    params   = []
    for case in manifest.get("cases", []):
        thresholds = _effective_thresholds(case, defaults)
        params.append(pytest.param(
            case["case_id"],
            str(_resolve_path(case["template_docx"])),
            str(_resolve_path(case["debug_json"])),
            thresholds,
            id=case["case_id"],
        ))
    return params


_REGRESSION_PARAMS = _build_pytest_params()


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "case_id, template_docx, debug_json, thresholds",
    _REGRESSION_PARAMS,
)
def test_changed_content_regression(
    case_id: str,
    template_docx: str,
    debug_json: str,
    thresholds: dict,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """End-to-end regression test for one template + debug-JSON pair.

    Runs the real post-processing pipeline (no LLM calls) and asserts that
    layout quality stays above calibrated thresholds.
    """
    # Skip gracefully if required sample files are missing
    for label, path in [("template_docx", template_docx), ("debug_json", debug_json)]:
        if not Path(path).exists():
            pytest.skip(f"Sample file not found ({label}): {path}")

    pdf_method = _detect_pdf_method()
    artifacts_root = _REPO_ROOT / "tmp" / "artefacts" / "postprocessing"
    output_dir = str(artifacts_root / case_id)

    result = run_regression_case(
        case_id=case_id,
        template_docx=template_docx,
        debug_json=debug_json,
        pdf_method=pdf_method,
        output_dir=output_dir,
    )

    diag = result.diagnostic()

    # ── Numeric threshold assertions ────────────────────────────────────
    min_layout = thresholds.get("min_layout_score", 0.40)
    assert result.composite >= min_layout, (
        f"[{case_id}] composite layout score {result.composite:.3f} "
        f"below floor {min_layout:.2f}\n{diag}"
    )

    min_topo = thresholds.get("min_topology_preservation", 0.00)
    if min_topo > 0.0:
        assert result.topology >= min_topo, (
            f"[{case_id}] topology preservation {result.topology:.3f} "
            f"below floor {min_topo:.2f}\n{diag}"
        )

    min_placement = thresholds.get("min_section_placement", 0.00)
    if min_placement > 0.0:
        assert result.placement >= min_placement, (
            f"[{case_id}] section placement {result.placement:.3f} "
            f"below floor {min_placement:.2f}\n{diag}"
        )

    min_coherence = thresholds.get("min_section_coherence", 0.40)
    if min_coherence > 0.0:
        assert result.coherence >= min_coherence, (
            f"[{case_id}] section coherence {result.coherence:.3f} "
            f"below floor {min_coherence:.2f}\n{diag}"
        )

    max_stress = thresholds.get("max_high_stress_sections", 6)
    assert result.high_stress_count <= max_stress, (
        f"[{case_id}] {result.high_stress_count} high-stress sections "
        f"exceeds limit {max_stress}\n{diag}"
    )

    # ── Hard-failure conditions ──────────────────────────────────────────
    # Catastrophic floor — anything this low signals a broken pipeline
    assert result.composite >= 0.20, (
        f"[{case_id}] CATASTROPHIC: composite {result.composite:.3f} < 0.20 — "
        f"pipeline may be broken\n{diag}"
    )


# ---------------------------------------------------------------------------
# Manifest unit tests
# ---------------------------------------------------------------------------

class TestManifestLoading:

    def test_manifest_exists(self):
        assert _MANIFEST.exists(), f"Regression manifest not found: {_MANIFEST}"

    def test_manifest_parses(self):
        data = _load_manifest()
        assert isinstance(data, dict)
        assert "cases" in data

    def test_all_cases_have_required_keys(self):
        data = _load_manifest()
        required = {"case_id", "template_docx", "debug_json"}
        for case in data["cases"]:
            missing = required - set(case.keys())
            assert not missing, (
                f"Case {case.get('case_id', '?')} missing keys: {missing}"
            )

    def test_all_template_paths_are_strings(self):
        data = _load_manifest()
        for case in data["cases"]:
            assert isinstance(case["template_docx"], str), (
                f"Case {case['case_id']}: template_docx must be a string"
            )
            assert isinstance(case["debug_json"], str), (
                f"Case {case['case_id']}: debug_json must be a string"
            )

    def test_effective_thresholds_uses_defaults(self):
        defaults = {"min_layout_score": 0.45, "max_high_stress_sections": 3}
        case     = {"case_id": "x", "template_docx": "", "debug_json": ""}
        merged   = _effective_thresholds(case, defaults)
        assert merged["min_layout_score"] == 0.45
        assert merged["max_high_stress_sections"] == 3

    def test_effective_thresholds_per_case_overrides_default(self):
        defaults  = {"min_layout_score": 0.45, "max_high_stress_sections": 3}
        case      = {"case_id": "x", "thresholds": {"min_layout_score": 0.70}}
        merged    = _effective_thresholds(case, defaults)
        assert merged["min_layout_score"] == 0.70     # overridden
        assert merged["max_high_stress_sections"] == 3 # kept from defaults

    def test_defaults_present_in_manifest(self):
        data = _load_manifest()
        assert "defaults" in data
        assert "min_layout_score" in data["defaults"]

    def test_manifest_case_count_matches_sample_pairs(self):
        """All 4 initial sample pairs must be registered."""
        data = _load_manifest()
        assert len(data["cases"]) >= 4, (
            f"Expected >= 4 cases, found {len(data['cases'])}"
        )


# ---------------------------------------------------------------------------
# Debug JSON extraction unit tests
# ---------------------------------------------------------------------------

class TestDebugJsonExtraction:

    def test_load_resume_text_succeeds(self, tmp_path):
        debug = tmp_path / "test.json"
        debug.write_text(json.dumps({
            "llm_response": {"resume": "John Doe\nExperience\n- Did stuff"}
        }), encoding="utf-8")
        text = load_resume_text(str(debug))
        assert "John Doe" in text

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_resume_text(str(tmp_path / "nonexistent.json"))

    def test_malformed_json_raises_value_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="Cannot parse"):
            load_resume_text(str(bad))

    def test_missing_llm_response_raises_key_error(self, tmp_path):
        debug = tmp_path / "d.json"
        debug.write_text(json.dumps({"company": "Acme"}), encoding="utf-8")
        with pytest.raises(KeyError, match="llm_response"):
            load_resume_text(str(debug))

    def test_missing_resume_key_raises_key_error(self, tmp_path):
        debug = tmp_path / "d.json"
        debug.write_text(json.dumps({"llm_response": {"cover_letter": "..."}}), encoding="utf-8")
        with pytest.raises(KeyError, match="resume"):
            load_resume_text(str(debug))

    def test_empty_resume_raises_value_error(self, tmp_path):
        debug = tmp_path / "d.json"
        debug.write_text(json.dumps({"llm_response": {"resume": "   "}}), encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            load_resume_text(str(debug))

    def test_real_debug_json_extracts_resume(self):
        """All real debug JSONs in the sample folder must yield non-empty resume text."""
        gen_dir = _REPO_ROOT / "tests" / "samples" / "generation"
        if not gen_dir.exists():
            pytest.skip("samples/generation not found")
        for json_path in sorted(gen_dir.glob("*.json")):
            text = load_resume_text(str(json_path))
            assert len(text) > 100, (
                f"Resume text suspiciously short in {json_path.name}: {len(text)} chars"
            )


# ---------------------------------------------------------------------------
# PDF method detection test
# ---------------------------------------------------------------------------

class TestPdfMethodDetection:

    def test_detect_returns_valid_method(self):
        method = _detect_pdf_method()
        assert method in ("local", "subprocess", "docker")

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("CC_REGRESSION_PDF_METHOD", "local")
        assert _detect_pdf_method() == "local"

    def test_unknown_env_value_falls_through(self, monkeypatch):
        monkeypatch.setenv("CC_REGRESSION_PDF_METHOD", "bogus")
        method = _detect_pdf_method()
        assert method in ("local", "subprocess", "docker")
