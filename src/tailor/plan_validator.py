"""Extended validator for Phase 1 (Planner) output — v2.1 schema checks.

Checks the five new top-level keys introduced in v2.1:
  theme_priority, role_repositioning_intent, domain_de_emphasis,
  evidence_saturation_rules, bullet_allocation_plan.

All checks are deterministic (no LLM).  Returns a list of error strings;
empty list means valid.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# New required keys in v2.1
# ---------------------------------------------------------------------------
_V21_REQUIRED_KEYS = {
    "theme_priority",
    "role_repositioning_intent",
    "domain_de_emphasis",
    "evidence_saturation_rules",
    "bullet_allocation_plan",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_plan_extended(plan: dict) -> list[str]:
    """Run v2.1 extended checks on a TailoringPlan dict.

    Returns a list of error strings.  Empty list means the plan is valid.
    The caller is responsible for ensuring basic v1 checks (validate_plan)
    already passed before calling this function.
    """
    if not isinstance(plan, dict):
        return ["plan must be a dict"]

    errors: list[str] = []

    # --- 1. New required keys present ---
    missing = _V21_REQUIRED_KEYS - plan.keys()
    if missing:
        errors.append(f"Plan missing v2.1 required keys: {sorted(missing)}")
        # Cannot run deeper checks without the keys
        return errors

    # Collect known theme names from jd_top_themes for cross-reference
    known_themes: set[str] = {
        t.get("theme", "")
        for t in plan.get("jd_top_themes", [])
        if isinstance(t, dict) and t.get("theme")
    }

    # --- 2. theme_priority tiers reference only known themes ---
    tp = plan.get("theme_priority", {})
    if not isinstance(tp, dict):
        errors.append("theme_priority must be an object")
    else:
        for tier in ("primary", "secondary", "supporting"):
            tier_themes = tp.get(tier, [])
            if not isinstance(tier_themes, list):
                errors.append(f"theme_priority.{tier} must be a list")
                continue
            for theme in tier_themes:
                if theme not in known_themes:
                    errors.append(
                        f"theme_priority.{tier} references unknown theme {theme!r} "
                        f"(not in jd_top_themes)"
                    )

        # Minimum tier population: at least 1 primary, at least 1 secondary
        primary = tp.get("primary", [])
        secondary = tp.get("secondary", [])
        if not isinstance(primary, list) or len(primary) < 1:
            errors.append("theme_priority.primary must have at least 1 theme")
        if not isinstance(secondary, list) or len(secondary) < 1:
            errors.append("theme_priority.secondary must have at least 1 theme")

    # --- 3. Evidence saturation per tier ---
    esr = plan.get("evidence_saturation_rules", {})
    if not isinstance(esr, dict):
        errors.append("evidence_saturation_rules must be an object")
    else:
        primary_min: int = esr.get("primary_theme_min_evidence", 3)
        secondary_min: int = esr.get("secondary_theme_min_evidence", 2)
        supporting_min: int = esr.get("supporting_theme_min_evidence", 1)

        # Build evidence-count map: theme -> number of evidence items
        evidence_count: dict[str, int] = {}
        for entry in plan.get("evidence_map", []):
            if isinstance(entry, dict):
                theme = entry.get("theme", "")
                evs = entry.get("evidence", [])
                if theme:
                    evidence_count[theme] = len(evs) if isinstance(evs, list) else 0

        tp_safe = tp if isinstance(tp, dict) else {}
        tier_minimums: dict[str, tuple[list, int]] = {
            "primary":   (tp_safe.get("primary", []),   primary_min),
            "secondary": (tp_safe.get("secondary", []), secondary_min),
            "supporting":(tp_safe.get("supporting", []),supporting_min),
        }
        for tier, (themes, min_ev) in tier_minimums.items():
            if not isinstance(themes, list):
                continue
            for theme in themes:
                count = evidence_count.get(theme, 0)
                if count < min_ev:
                    errors.append(
                        f"evidence_saturation: theme {theme!r} ({tier}) has {count} "
                        f"evidence item(s); minimum is {min_ev}"
                    )

    # --- 4. role_repositioning_intent covers top 2 roles ---
    rri = plan.get("role_repositioning_intent", [])
    if not isinstance(rri, list):
        errors.append("role_repositioning_intent must be a list")
    else:
        experience = plan.get("resume_strategy", {}).get("experience", [])
        top_2_roles = [
            _normalize_role_name(e.get("role_name", ""))
            for e in experience[:2]
            if isinstance(e, dict) and e.get("role_name")
        ]
        rri_names = {
            _normalize_role_name(r.get("role_name", ""))
            for r in rri
            if isinstance(r, dict) and r.get("role_name")
        }
        for normalized in top_2_roles:
            if normalized not in rri_names:
                orig = next(
                    (
                        e.get("role_name", normalized)
                        for e in experience[:2]
                        if _normalize_role_name(e.get("role_name", "")) == normalized
                    ),
                    normalized,
                )
                errors.append(
                    f"role_repositioning_intent missing entry for top role {orig!r}"
                )

    # --- 5. domain_de_emphasis required fields when enabled ---
    dde = plan.get("domain_de_emphasis", {})
    if not isinstance(dde, dict):
        errors.append("domain_de_emphasis must be an object")
    elif dde.get("enabled"):
        dwt = dde.get("downweight_terms", [])
        prf = dde.get("preferred_replacement_frame", "")
        if not isinstance(dwt, list) or len(dwt) == 0:
            errors.append(
                "domain_de_emphasis.enabled=true but downweight_terms is empty"
            )
        if not isinstance(prf, str) or not prf.strip():
            errors.append(
                "domain_de_emphasis.enabled=true but preferred_replacement_frame is empty"
            )

    # --- 6. bullet_allocation_plan covers top 2 roles with >= 2 themes each ---
    bap = plan.get("bullet_allocation_plan", [])
    if not isinstance(bap, list):
        errors.append("bullet_allocation_plan must be a list")
    else:
        experience = plan.get("resume_strategy", {}).get("experience", [])
        top_2_roles = [
            _normalize_role_name(e.get("role_name", ""))
            for e in experience[:2]
            if isinstance(e, dict) and e.get("role_name")
        ]
        bap_by_role: dict[str, dict] = {}
        for entry in bap:
            if isinstance(entry, dict) and entry.get("role_name"):
                bap_by_role[_normalize_role_name(entry["role_name"])] = entry

        for normalized in top_2_roles:
            if normalized not in bap_by_role:
                orig = next(
                    (
                        e.get("role_name", normalized)
                        for e in experience[:2]
                        if _normalize_role_name(e.get("role_name", "")) == normalized
                    ),
                    normalized,
                )
                errors.append(
                    f"bullet_allocation_plan missing entry for top role {orig!r}"
                )
            else:
                ttmb = bap_by_role[normalized].get("theme_to_min_bullets", {})
                theme_count = len(ttmb) if isinstance(ttmb, dict) else 0
                if theme_count < 2:
                    orig_name = bap_by_role[normalized].get("role_name", normalized)
                    errors.append(
                        f"bullet_allocation_plan entry for {orig_name!r} has "
                        f"{theme_count} theme(s); at least 2 required"
                    )

    return errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_role_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy role matching."""
    s = name.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
