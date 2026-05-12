#!/usr/bin/env python3
"""Layout-bound IR health checker.

Usage (from debug JSON):
    python scripts/check_layout_bound_ir_health.py <debug_json_path>

Usage (programmatic):
    from scripts.check_layout_bound_ir_health import check_layout_bound_ir_health
    violations = check_layout_bound_ir_health(updated_ir_dict)

The checker inspects updated_ir exactly as serialized and asserts all
layout-bound structural invariants.  Exits 1 if any hard violation exists.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))


# ---------------------------------------------------------------------------
# Core checker (works on a deserialized ResumeDocument object)
# ---------------------------------------------------------------------------

def check_layout_bound_ir_health(doc: "ResumeDocument") -> dict:  # type: ignore[name-defined]
    """Check layout-bound invariants on a ResumeDocument.

    Returns a dict with:
      A. non_empty_unbound_semantic_paras  — para_id='' with text (INFO: overflow reflow)
      B. synthetic_sections               — section_id='' with content
      C. unbound_layout_blocks            — LayoutParagraphBlock with para_id=''
      D. split_brain_experience           — experience has roles AND role-like body_paras
      E. role_bullets_unbound             — role bullet with para_id='' (INFO: overflow reflow)
      F. role_count                       — total experience roles across all sections
      G. layout_blocks_count              — total layout_blocks entries
      H. layout_semantic_mismatches       — layout para_ids not in semantic model

    Hard violations (must be 0): B, C, D
    Info: A, E, F, G, H
    Note: A and E are intentionally allowed — unbound paras are overflow-reflow content
          that flows to continuation pages via the renderer.
    """
    from tailor.compiler.models import LayoutParagraphBlock, LayoutTableBlock

    violations: dict = {
        "non_empty_unbound_semantic_paras": 0,
        "synthetic_sections": 0,
        "unbound_layout_blocks": 0,
        "split_brain_experience": 0,
        "role_bullets_unbound": 0,
        "role_count": 0,
        "layout_blocks_count": len(doc.layout_blocks) if doc.layout_blocks else 0,
        "layout_semantic_mismatches": 0,
    }

    # ── Collect all semantic paragraphs ──────────────────────────────────
    def _iter_semantic_paras():
        yield from doc.header_paras
        for sec in doc.sections:
            yield sec.heading
            for role in sec.roles:
                yield role.header
                yield from role.header_extra
                yield from role.meta_lines
                yield from role.bullets
            yield from sec.body_paras

    # A: non-empty para_id=""
    for pm in _iter_semantic_paras():
        if not pm.para_id and pm.text.strip():
            violations["non_empty_unbound_semantic_paras"] += 1
            _note(f"A: unbound [{pm.semantic}] {pm.text[:60]!r}")

    # B: synthetic sections
    for sec in doc.sections:
        has_content = (
            any(p.text.strip() for p in sec.body_paras)
            or any(r.header.text.strip() for r in sec.roles)
        )
        if not sec.section_id and has_content:
            violations["synthetic_sections"] += 1
            _note(f"B: synthetic section {sec.title!r}")

    # C: layout_blocks with para_id=""
    if doc.layout_blocks:
        for block in doc.layout_blocks:
            if isinstance(block, LayoutParagraphBlock) and not block.para_id:
                violations["unbound_layout_blocks"] += 1
                xml_snip = (block.xml_proto_xml or "")[:80]
                _note(f"C: LB block with empty para_id, xml={xml_snip!r}")

    # D: split-brain experience
    _ROLE_SEMANTICS = frozenset({"role_header", "role_meta", "bullet"})
    for sec in doc.sections:
        if sec.semantic_type == "experience" and sec.roles:
            role_like_body = [
                p for p in sec.body_paras
                if p.text.strip() and p.semantic in _ROLE_SEMANTICS
            ]
            if role_like_body:
                violations["split_brain_experience"] += 1
                _note(
                    f"D: split-brain in {sec.title!r}: {len(role_like_body)} role-like "
                    f"paras in body_paras while roles={len(sec.roles)}"
                )

    # E: unbound role bullets
    for sec in doc.sections:
        violations["role_count"] += len(sec.roles)
        for role in sec.roles:
            for b in role.bullets:
                if not b.para_id and b.text.strip():
                    violations["role_bullets_unbound"] += 1
                    _note(f"E: unbound bullet in role {role.role_id[:40]!r}: {b.text[:50]!r}")

    # H: layout/semantic para_id mismatches (layout para_ids not in semantic model)
    if doc.layout_blocks:
        semantic_ids = {pm.para_id for pm in _iter_semantic_paras() if pm.para_id}
        for block in doc.layout_blocks:
            if isinstance(block, LayoutTableBlock):
                for pid in block.para_ids:
                    if pid and not pid.startswith("lb_orphan_") and pid not in semantic_ids:
                        violations["layout_semantic_mismatches"] += 1
            elif isinstance(block, LayoutParagraphBlock):
                pid = block.para_id
                if pid and not pid.startswith("lb_orphan_") and pid not in semantic_ids:
                    violations["layout_semantic_mismatches"] += 1

    return violations


_notes: list[str] = []

def _note(msg: str) -> None:
    _notes.append(msg)
    print(f"  VIOLATION: {msg}")


def _print_summary(doc: "ResumeDocument", violations: dict) -> None:
    """Print a human-readable summary."""
    print(f"\n{'='*60}")
    print("  IR HEALTH REPORT (layout-bound invariants)")
    print(f"{'='*60}")
    print(f"  layout_blocks_count          : {violations['layout_blocks_count']}")
    print(f"  role_count (all experience)  : {violations['role_count']}")

    # A (non_empty_unbound_semantic_paras) and E (role_bullets_unbound) are intentionally
    # info-only: unbound paras are overflow-reflow content that flows to continuation pages.
    _INFO_KEYS = frozenset({
        "role_count", "layout_blocks_count", "layout_semantic_mismatches",
        "non_empty_unbound_semantic_paras", "role_bullets_unbound",
    })
    hard = {k: v for k, v in violations.items() if k not in _INFO_KEYS}
    total_hard = sum(hard.values())
    info = {
        "layout_semantic_mismatches": violations["layout_semantic_mismatches"],
        "non_empty_unbound_semantic_paras (overflow)": violations["non_empty_unbound_semantic_paras"],
        "role_bullets_unbound (overflow)": violations["role_bullets_unbound"],
    }

    print(f"\n  Hard violations (must be 0):")
    for k, v in hard.items():
        flag = "FAIL" if v else "ok  "
        print(f"    {flag}  {k}: {v}")
    print(f"\n  Info:")
    for k, v in info.items():
        print(f"    info  {k}: {v}")

    print(f"\n  Sections ({len(doc.sections)}):")
    for sec in doc.sections:
        sid = sec.section_id if sec.section_id else "'' <MISSING>"
        print(f"    [{sec.semantic_type:12}] {sec.title[:35]:35}  id={sid}  roles={len(sec.roles)}  body={len(sec.body_paras)}")

    print(f"\n  Experience roles:")
    for sec in doc.sections:
        if sec.semantic_type == "experience":
            for role in sec.roles:
                ub = sum(1 for b in role.bullets if not b.para_id and b.text.strip())
                print(f"    {role.header.text[:45]:45}  h_pid={role.header.para_id!r}  bullets={len(role.bullets)}  unbound={ub}")

    verdict = "ALL HARD INVARIANTS PASS" if total_hard == 0 else f"{total_hard} HARD VIOLATION(S)"
    print(f"\n  VERDICT: {verdict}")


# ---------------------------------------------------------------------------
# Entry point: from debug JSON
# ---------------------------------------------------------------------------

def check_from_debug_json(debug_json_path: str) -> dict:
    """Load updated_ir from a debug JSON file and run health checks."""
    from tailor.compiler.models import ResumeDocument

    with open(debug_json_path, encoding="utf-8") as f:
        debug = json.load(f)

    ir_data = debug.get("updated_ir")
    if ir_data is None:
        # Try direct IR dict
        ir_data = debug

    doc = ResumeDocument.from_dict(ir_data)
    _notes.clear()
    violations = check_layout_bound_ir_health(doc)
    _print_summary(doc, violations)
    return violations


# ---------------------------------------------------------------------------
# Programmatic entry point for tests
# ---------------------------------------------------------------------------

def assert_layout_bound_clean(
    doc: "ResumeDocument",
    label: str = "IR",
    *,
    expected_experience_roles: int | None = None,
) -> None:
    """Assert all hard layout-bound invariants on *doc*.

    Raises AssertionError with a detailed message if any hard violation exists.
    Optionally checks that the total experience role count equals
    *expected_experience_roles*.
    """
    _notes.clear()
    violations = check_layout_bound_ir_health(doc)
    _print_summary(doc, violations)

    # A and E are info-only (overflow reflow content, intentionally unbound)
    _INFO_KEYS_ASSERT = frozenset({
        "role_count", "layout_blocks_count", "layout_semantic_mismatches",
        "non_empty_unbound_semantic_paras", "role_bullets_unbound",
    })
    hard = {k: v for k, v in violations.items() if k not in _INFO_KEYS_ASSERT}
    failures = {k: v for k, v in hard.items() if v}

    if expected_experience_roles is not None:
        actual = violations["role_count"]
        if actual != expected_experience_roles:
            failures[f"role_count (expected {expected_experience_roles})"] = actual

    assert not failures, (
        f"{label} layout-bound IR violations:\n"
        + "\n".join(f"  {k}: {v}" for k, v in failures.items())
        + "\nDetails:\n"
        + "\n".join(f"  {n}" for n in _notes)
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_layout_bound_ir_health.py <debug_json_path>")
        sys.exit(1)
    violations = check_from_debug_json(sys.argv[1])
    _INFO = frozenset({
        "role_count", "layout_blocks_count", "layout_semantic_mismatches",
        "non_empty_unbound_semantic_paras", "role_bullets_unbound",
    })
    hard = {k: v for k, v in violations.items() if k not in _INFO}
    sys.exit(0 if all(v == 0 for v in hard.values()) else 1)


if __name__ == "__main__":
    main()
