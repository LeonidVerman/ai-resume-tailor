"""IR Validation — structural checks on the generated IR JSON.

Hard-fail codes (unbound visible content / missing sections):
  EMPTY_PARA_ID           non-empty para with para_id == ""
  EXPERIENCE_NO_ROLES     experience section with an empty roles list

Soft checks (penalty only — no hard fail):
  DUPLICATE_PARA_ID       same para_id used on two or more paragraphs
  EMPTY_SECTION_ID        section with section_id == ""
  SPLIT_BRAIN_BODY_PARAS  para_id appears in both body_paras and a role
  CURRENT_DATE_LEAKAGE    literal "current date" / "currentdate" in any para
  ROLE_BOUNDARY_CORRUPTION role bullet has semantic=role_header/section_heading
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Failures that trigger hard-fail (unbound visible content, missing sections).
_HARD_FAIL_CODES: frozenset[str] = frozenset({
    "EMPTY_PARA_ID",
    "EXPERIENCE_NO_ROLES",
})

# Per-failure-type penalty deducted from ir_score (100 → 0).
_SEVERITY: dict[str, int] = {
    "DUPLICATE_PARA_ID": 35,
    "SPLIT_BRAIN_BODY_PARAS": 25,
    "ROLE_BOUNDARY_CORRUPTION": 20,
    "CURRENT_DATE_LEAKAGE": 15,
    "EMPTY_SECTION_ID": 15,
}


@dataclass
class IRValidationResult:
    passed: bool
    hard_fail: bool
    failures: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _iter_all_paras(ir: dict):
    """Yield every paragraph dict in the IR in document order."""
    for p in ir.get("header_paras", []):
        yield p
    for sec in ir.get("sections", []):
        h = sec.get("heading")
        if h:
            yield h
        for p in sec.get("body_paras", []):
            yield p
        for role in sec.get("roles", []):
            rh = role.get("header")
            if rh:
                yield rh
            for p in role.get("header_extra", []):
                yield p
            for p in role.get("meta_lines", []):
                yield p
            for p in role.get("bullets", []):
                yield p


def validate_ir(ir: dict) -> IRValidationResult:
    """Run all structural checks and return a validation result."""
    failures: list[str] = []
    evidence: list[str] = []
    seen_ids: set[str] = set()

    # ── Pass 1: para-level checks ─────────────────────────────────────────────
    for p in _iter_all_paras(ir):
        pid = p.get("para_id", "")
        text = (p.get("text") or "").strip()

        if text and not pid:
            if "EMPTY_PARA_ID" not in failures:
                failures.append("EMPTY_PARA_ID")
            evidence.append(f"Non-empty para has empty para_id: {text[:60]!r}")

        if pid:
            if pid in seen_ids:
                if "DUPLICATE_PARA_ID" not in failures:
                    failures.append("DUPLICATE_PARA_ID")
                    evidence.append(f"Duplicate para_id: {pid!r}")
            seen_ids.add(pid)

    # ── Pass 2: section-level checks ──────────────────────────────────────────
    for sec in ir.get("sections", []):
        sec_id = sec.get("section_id", "")
        if not sec_id:
            if "EMPTY_SECTION_ID" not in failures:
                failures.append("EMPTY_SECTION_ID")
            evidence.append(f"Section missing section_id: title={sec.get('title', '')!r}")

        if sec.get("semantic_type") == "experience":
            roles = sec.get("roles", [])
            if not roles:
                # Date-first templates store content in body_paras (roles=[]).
                # Only hard-fail when body_paras also have no meaningful text.
                body_content = any(
                    p.get("text", "").strip()
                    for p in sec.get("body_paras", [])
                )
                if not body_content:
                    failures.append("EXPERIENCE_NO_ROLES")
                    evidence.append(
                        f"Experience section {sec.get('title', '')!r} has no roles and no body content"
                    )
                continue

            # Split-brain: para_id in body_paras also appears in a role sub-list
            body_ids = {
                p.get("para_id")
                for p in sec.get("body_paras", [])
                if p.get("para_id")
            }
            role_ids: set[str] = set()
            for role in roles:
                rh = role.get("header")
                if rh and rh.get("para_id"):
                    role_ids.add(rh["para_id"])
                for list_field in ("header_extra", "meta_lines", "bullets"):
                    for p in role.get(list_field, []):
                        if p.get("para_id"):
                            role_ids.add(p["para_id"])

            overlap = body_ids & role_ids
            if overlap:
                if "SPLIT_BRAIN_BODY_PARAS" not in failures:
                    failures.append("SPLIT_BRAIN_BODY_PARAS")
                evidence.append(
                    f"Experience body_paras share para_ids with roles: "
                    f"{sorted(overlap)[:3]}"
                )

            # Role boundary corruption: bullet paragraph has section-level semantic
            for role in roles:
                for bullet in role.get("bullets", []):
                    sem = bullet.get("semantic", "")
                    if sem in ("role_header", "section_heading"):
                        if "ROLE_BOUNDARY_CORRUPTION" not in failures:
                            failures.append("ROLE_BOUNDARY_CORRUPTION")
                            evidence.append(
                                f"Bullet has semantic={sem!r} in role "
                                f"{role.get('role_id_stable', '')!r}"
                            )

    # ── Pass 3: content leakage ───────────────────────────────────────────────
    for p in _iter_all_paras(ir):
        text = p.get("text", "").lower()
        if "current date" in text or "currentdate" in text:
            if "CURRENT_DATE_LEAKAGE" not in failures:
                failures.append("CURRENT_DATE_LEAKAGE")
                evidence.append(
                    f"Current Date leakage: {p.get('text', '')[:80]!r}"
                )

    return IRValidationResult(
        passed=len(failures) == 0,
        hard_fail=any(f in _HARD_FAIL_CODES for f in failures),
        failures=failures,
        evidence=evidence,
    )


def ir_score(result: IRValidationResult) -> float:
    """Convert a validation result into a 0–100 numeric score."""
    if result.passed:
        return 100.0
    penalty = sum(_SEVERITY.get(f, 30) for f in result.failures)
    return max(0.0, 100.0 - float(penalty))
