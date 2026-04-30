#!/usr/bin/env python3
"""IR Health Check — prints a compact structural report for a given DOCX template.

Usage:
    python scripts/ir_health_check.py <path_to.docx> [--layout-bound]

With --layout-bound:
    Sets USE_LAYOUT_BOUND_UPDATER=True and USE_LAYOUT_BLOCK_RENDERER=True before
    running apply_tailored with the template's own content as synthetic LLM input.
    This simulates a real tailoring run without invoking the LLM.

The report covers all global layout-bound invariants:
  1. Non-empty para_id="" count
  2. Section section_id="" count
  3. Synthetic/unanchored sections
  4. layout_blocks para_id consistency
  5. Role cardinality and boundary violations
  6. Paragraph density overflow
"""
from __future__ import annotations

import sys
import os
import argparse
from pathlib import Path

# Ensure the project src is on PYTHONPATH
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))


def _build_synthetic_llm_sections(doc) -> list:
    """Build LLM sections from the parsed template's own content (identity transform)."""
    from tailor.compiler.text_parser import LlmSection, LlmRole
    llm_sections = []
    for sec in doc.sections:
        if sec.semantic_type in ("education", "certifications", "languages", "websites"):
            continue  # locked — skip
        if sec.semantic_type == "experience" and sec.roles:
            roles = [
                LlmRole(
                    header=role.header.text,
                    meta_lines=[m.text for m in role.meta_lines],
                    bullets=[b.text for b in role.bullets],
                )
                for role in sec.roles
            ]
            llm_sections.append(LlmSection(
                heading=sec.title,
                semantic_type=sec.semantic_type,
                roles=roles,
            ))
        else:
            llm_sections.append(LlmSection(
                heading=sec.title,
                semantic_type=sec.semantic_type,
                body_lines=[p.text for p in sec.body_paras if p.text.strip()],
            ))
    return llm_sections


def _collect_all_semantic_paras(doc) -> list:
    """Collect all paragraphs from the semantic model."""
    paras = list(doc.header_paras)
    for sec in doc.sections:
        paras.append(sec.heading)
        for role in sec.roles:
            paras.append(role.header)
            paras.extend(role.header_extra)
            paras.extend(role.meta_lines)
            paras.extend(role.bullets)
        paras.extend(sec.body_paras)
    return paras


def _top_expansions(original, updated, n: int = 10) -> list[tuple]:
    """Return top-N paragraphs with the largest text expansion ratio."""
    orig_map = {pm.para_id: pm for pm in _collect_all_semantic_paras(original) if pm.para_id}
    updated_map = {pm.para_id: pm for pm in _collect_all_semantic_paras(updated) if pm.para_id}
    ratios = []
    for pid, updated_pm in updated_map.items():
        orig_pm = orig_map.get(pid)
        if orig_pm is None:
            continue
        orig_len = max(len(orig_pm.text), 1)
        updated_len = len(updated_pm.text)
        ratio = updated_len / orig_len
        if ratio > 1.01:
            ratios.append((ratio, pid, orig_len, updated_len, updated_pm.text[:80]))
    ratios.sort(reverse=True)
    return ratios[:n]


def ir_health_report(
    sample_path: str,
    layout_bound: bool = True,
    verbose: bool = True,
) -> dict:
    """Run the IR health check for a given DOCX sample. Returns a violations dict."""
    import tailor.config as cfg

    # Override flags
    original_lb = cfg.USE_LAYOUT_BOUND_UPDATER
    original_lbr = cfg.USE_LAYOUT_BLOCK_RENDERER
    if layout_bound:
        cfg.USE_LAYOUT_BOUND_UPDATER = True
        cfg.USE_LAYOUT_BLOCK_RENDERER = True

    try:
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import (
            apply_tailored,
            validate_layout_binding,
            validate_structural_integrity,
            validate_layout_density,
        )
        from tailor.compiler.models import LayoutParagraphBlock, LayoutTableBlock

        original = parse_docx(sample_path)
        llm_sections = _build_synthetic_llm_sections(original)
        updated = apply_tailored(original, llm_sections)

        # ── Header ──────────────────────────────────────────────────────────
        sample_name = Path(sample_path).stem[:50]
        lb_flag = "LAYOUT_BOUND=ON" if layout_bound else "LAYOUT_BOUND=OFF"
        if verbose:
            print(f"\n{'='*70}")
            print(f"  IR HEALTH REPORT: {sample_name}")
            print(f"  {lb_flag}")
            print(f"{'='*70}")

        # ── Basic counts ─────────────────────────────────────────────────────
        all_semantic = _collect_all_semantic_paras(updated)
        n_semantic = len(all_semantic)
        n_unbound = sum(1 for p in all_semantic if not p.para_id and p.text.strip())
        lb_count = len(updated.layout_blocks) if updated.layout_blocks else 0

        # layout_blocks para_id sets
        lb_ids: set[str] = set()
        if updated.layout_blocks:
            for block in updated.layout_blocks:
                if isinstance(block, LayoutTableBlock):
                    lb_ids.update(pid for pid in block.para_ids if pid)
                elif isinstance(block, LayoutParagraphBlock) and block.para_id:
                    lb_ids.add(block.para_id)

        semantic_ids = {p.para_id for p in all_semantic if p.para_id}
        lb_missing_in_semantic = len(lb_ids - semantic_ids)   # in LB but not semantic
        semantic_missing_in_lb = len(semantic_ids - lb_ids)    # in semantic but not LB

        if verbose:
            print(f"\n  layout_blocks count : {lb_count}")
            print(f"  semantic para count : {n_semantic}")
            print(f"  NON-EMPTY para_id='' : {n_unbound}  {'VIOLATION' if n_unbound else ''}")
            print(f"  LB ids missing in semantic : {lb_missing_in_semantic}")
            print(f"  semantic ids missing in LB : {semantic_missing_in_lb}")

        # ── Sections ─────────────────────────────────────────────────────────
        n_no_section_id = 0
        n_synthetic = 0
        orig_section_ids = {s.section_id for s in original.sections if s.section_id}
        if verbose:
            print(f"\n  Sections ({len(updated.sections)}):")
        for sec in updated.sections:
            has_content = (
                any(p.text.strip() for p in sec.body_paras)
                or any(r.header.text.strip() for r in sec.roles)
            )
            missing_id = not sec.section_id
            synthetic = missing_id and has_content
            if missing_id and has_content:
                n_no_section_id += 1
            if sec.section_id and sec.section_id not in orig_section_ids:
                n_synthetic += 1
            id_flag = sec.section_id if sec.section_id else "'' <- MISSING"
            synth_flag = " <- SYNTHETIC" if synthetic else ""
            if verbose:
                print(f"    [{sec.semantic_type:12}] {sec.title[:35]:35} id={id_flag}{synth_flag}")
                print(f"      heading para_id={sec.heading.para_id!r:15}  body={len(sec.body_paras)}  roles={len(sec.roles)}")

        # ── Experience roles ──────────────────────────────────────────────────
        from tailor.compiler.updater import _has_date_first_layout
        exp_sections = [s for s in updated.sections if s.semantic_type == "experience"]
        orig_exp_sections = [s for s in original.sections if s.semantic_type == "experience"]
        total_orig_roles = sum(len(s.roles) for s in orig_exp_sections)
        total_updated_roles = sum(len(s.roles) for s in exp_sections)
        # Date-first layouts intentionally return roles=[] (content is in body_paras).
        # Don't flag these as cardinality violations.
        date_first_exp = [s for s in orig_exp_sections if _has_date_first_layout(s)]
        adj_orig_roles = total_orig_roles - sum(len(s.roles) for s in date_first_exp)
        adj_updated_roles = total_updated_roles
        role_cardinality_ok = adj_updated_roles == adj_orig_roles

        if verbose:
            note = "(date-first)" if date_first_exp else ""
            print(f"\n  Experience roles: {total_updated_roles} (expected {adj_orig_roles}) {note} {'' if role_cardinality_ok else 'VIOLATION'}")

        n_unbound_bullets = 0
        n_role_boundary_violations = 0
        for exp in exp_sections:
            for role in exp.roles:
                unbound_b = sum(1 for b in role.bullets if not b.para_id and b.text.strip())
                n_unbound_bullets += unbound_b
                rh_pid = role.header.para_id
                if verbose:
                    print(f"    Role: {role.header.text[:45]:45}  h_pid={rh_pid!r}")
                    for m in role.meta_lines:
                        print(f"      meta: {m.text[:50]:50}  pid={m.para_id!r}")
                    print(f"      bullets={len(role.bullets)}  unbound={unbound_b}")
                    for b in role.bullets[:3]:
                        packed_flag = " <- NEWLINE-PACKED" if "\n" in b.text else ""
                        print(f"      • {b.text[:60]:60} pid={b.para_id!r}{packed_flag}")
                    if len(role.bullets) > 3:
                        print(f"      ... +{len(role.bullets)-3} more bullets")
                # Check boundary violations
                from tailor.compiler.updater import _bullet_looks_like_role_title
                for b in role.bullets:
                    if _bullet_looks_like_role_title(b.text):
                        n_role_boundary_violations += 1
                        if verbose:
                            print(f"      <- ROLE BOUNDARY VIOLATION: {b.text[:60]!r}")

        # ── Density ──────────────────────────────────────────────────────────
        density = validate_layout_density(original, updated)
        if verbose:
            print(f"\n  Density: overflow={density['density_overflow_count']}  newline_packing={density['multi_bullet_packing_count']}")

        # ── Top expansions ────────────────────────────────────────────────────
        expansions = _top_expansions(original, updated)
        if verbose and expansions:
            print(f"\n  Top text expansions:")
            for ratio, pid, orig_len, updated_len, snippet in expansions[:5]:
                flag = " <- LARGE" if ratio > 2.0 else ""
                print(f"    {pid}: {orig_len}->{updated_len} ({ratio:.1f}×){flag}  {snippet!r}")

        # ── Summary verdict ───────────────────────────────────────────────────
        # Hard violations (must be 0 for acceptance):
        violations = {
            "unbound_non_empty_paras": n_unbound,
            "sections_missing_id": n_no_section_id,
            "synthetic_sections": n_synthetic,
            "role_cardinality_violation": 0 if role_cardinality_ok else 1,
            "unbound_bullets": n_unbound_bullets,
            "role_boundary_violations": n_role_boundary_violations,
            "density_overflow": density["density_overflow_count"],
            "newline_packing": density["multi_bullet_packing_count"],
        }
        total_violations = sum(violations.values())
        # Soft warnings (informational only):
        warnings = {
            "lb_missing_in_semantic": lb_missing_in_semantic,
            "semantic_missing_in_lb": semantic_missing_in_lb,
        }

        if verbose:
            print(f"\n  VERDICT: {total_violations} hard violation(s)")
            if total_violations == 0:
                print("   ALL HARD INVARIANTS PASS")
            else:
                for k, v in violations.items():
                    if v:
                        print(f"  FAIL {k}: {v}")
            if any(warnings.values()):
                for k, v in warnings.items():
                    if v:
                        print(f"  WARN {k}: {v}")

        return violations

    finally:
        cfg.USE_LAYOUT_BOUND_UPDATER = original_lb
        cfg.USE_LAYOUT_BLOCK_RENDERER = original_lbr


def main():
    parser = argparse.ArgumentParser(description="IR health check for DOCX samples")
    parser.add_argument("paths", nargs="+", help="DOCX file paths")
    parser.add_argument("--no-layout-bound", action="store_true",
                        help="Run without layout-bound mode (shows baseline)")
    args = parser.parse_args()

    all_ok = True
    for path in args.paths:
        v = ir_health_report(path, layout_bound=not args.no_layout_bound)
        if sum(v.values()) > 0:
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
