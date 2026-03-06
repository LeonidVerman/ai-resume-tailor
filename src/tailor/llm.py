"""OpenAI client wrapper and document tailoring logic."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from openai import OpenAI

from tailor.config import (
    ENABLE_PHASE2_JUDGE,
    PHASE1_MAX_TOKENS,
    PHASE1_MODEL,
    PHASE1_REPAIR_TEMPERATURE,
    PHASE1_TEMPERATURE,
    PHASE2_JUDGE_MODEL,
    PHASE2_MAX_REPAIR_ATTEMPTS,
    PHASE2_MAX_TOKENS,
    PHASE2_MODEL,
    PHASE2_REPAIR_TEMPERATURE,
    PHASE2_TEMPERATURE,
    SCHEMAS_DIR,
    SIMPLE_MODEL,
    SIMPLE_TEMPERATURE,
    load_domain_translation_rules,
)
from tailor.plan_validator import validate_plan_extended
from tailor.job import JobData
from tailor.phase2_validator import (
    LEDGER_MISMATCH_ERROR_PREFIX,
    apply_judge_to_validation,
    validate_phase2_output,
)
from tailor.prompts import _load_candidate_profile, _load_prompt, _load_prompt_optional
from tailor.writer_packet import build_writer_packet

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

# --- Allowed values ---
_VALID_ROLE_LEVELS = {"director", "manager", "principal", "staff", "senior", "mid", "junior"}
_VALID_PRIORITIES = {"high", "medium", "low"}
_VALID_EVIDENCE_SOURCES = {"candidate_profile", "master_resume"}

# ---------------------------------------------------------------------------
# Phase 1 output schema (loaded once from schemas/phase1_output.json)
# ---------------------------------------------------------------------------

_PHASE1_OUTPUT_SCHEMA: dict | None = None


def _get_phase1_schema() -> dict:
    """Return the Phase 1 output JSON Schema, loading it from disk on first call."""
    global _PHASE1_OUTPUT_SCHEMA
    if _PHASE1_OUTPUT_SCHEMA is None:
        schema_path = SCHEMAS_DIR / "phase1_output.json"
        with open(schema_path, encoding="utf-8") as f:
            _PHASE1_OUTPUT_SCHEMA = json.load(f)
    return _PHASE1_OUTPUT_SCHEMA


# Required top-level keys for a TailoringPlan — single source of truth.
# Must stay in sync with schemas/phase1_output.json "required" array.
_PLAN_REQUIRED_KEYS = frozenset({
    "role_level",
    "resume_mode",
    "jd_domain",
    "candidate_primary_domain",
    "domain_mismatch",
    "domain_translation_rule_ids",
    "jd_top_themes",
    "vocabulary_anchoring",
    "evidence_map",
    "resume_strategy",
    "cover_letter_strategy",
    "risk_checks",
    "narrative_plan",
    "skill_graph",
})


# ---------------------------------------------------------------------------
# Schema gate + role_level coercion (run BEFORE deep validation)
# ---------------------------------------------------------------------------

def _coerce_role_level(raw: str) -> str:
    """Map any role_level string to a valid enum value.

    Handles cases where the LLM returns a job title instead of the enum
    (e.g. "Senior Software Engineer" → "senior").
    Falls back to "junior" for anything unrecognised.

    Order matters — more-specific patterns are checked first.
    """
    s = raw.lower()
    if "director" in s or "vp" in s or "head of" in s:
        return "director"
    if "principal" in s:
        return "principal"
    if "staff" in s:
        return "staff"
    if "manager" in s:
        return "manager"
    if "senior" in s:
        return "senior"
    if "mid" in s or "intermediate" in s:
        return "mid"
    return "junior"


def _run_schema_gate(data: Any) -> list[str]:
    """Structural schema gate: required-key and type checks only.

    Returns a list of error strings; empty list means the schema is valid.
    Never raises.  Must be called before any deep validation to prevent
    crashes caused by missing or wrongly-typed fields.

    Errors are capped at 15 to avoid flooding the repair-prompt context.
    """
    if not isinstance(data, dict):
        return [f"schema_invalid: expected JSON object, got {type(data).__name__}"]

    errors: list[str] = []

    # All required keys must be present before sub-structure checks.
    missing = _PLAN_REQUIRED_KEYS - data.keys()
    if missing:
        errors.append(f"schema_invalid: missing keys: {sorted(missing)}")
        return errors  # sub-checks on absent keys would be misleading

    # role_level: must be a string (value is normalised by _coerce_role_level)
    if not isinstance(data["role_level"], str):
        errors.append(
            f"schema_invalid: role_level must be a string, "
            f"got {type(data['role_level']).__name__}"
        )

    # domain fields
    for str_key in ("jd_domain", "candidate_primary_domain"):
        if not isinstance(data.get(str_key), str):
            errors.append(
                f"schema_invalid: {str_key} must be a string, "
                f"got {type(data.get(str_key)).__name__}"
            )
    if not isinstance(data.get("domain_mismatch"), bool):
        errors.append(
            f"schema_invalid: domain_mismatch must be a boolean, "
            f"got {type(data.get('domain_mismatch')).__name__}"
        )
    if not isinstance(data.get("domain_translation_rule_ids"), list):
        errors.append(
            f"schema_invalid: domain_translation_rule_ids must be a list, "
            f"got {type(data.get('domain_translation_rule_ids')).__name__}"
        )

    # jd_top_themes: list of objects each with required keys
    jdt = data["jd_top_themes"]
    if not isinstance(jdt, list):
        errors.append(
            f"schema_invalid: jd_top_themes must be a list, got {type(jdt).__name__}"
        )
    else:
        for i, item in enumerate(jdt):
            if not isinstance(item, dict):
                errors.append(f"schema_invalid: jd_top_themes[{i}] must be an object")
            else:
                for k in ("theme", "why_important", "keywords"):
                    if k not in item:
                        errors.append(
                            f"schema_invalid: jd_top_themes[{i}] missing key {k!r}"
                        )

    # evidence_map: list of objects, each with an evidence list of objects
    em = data["evidence_map"]
    if not isinstance(em, list):
        errors.append(
            f"schema_invalid: evidence_map must be a list, got {type(em).__name__}"
        )
    else:
        for i, entry in enumerate(em):
            if not isinstance(entry, dict):
                errors.append(f"schema_invalid: evidence_map[{i}] must be an object")
                continue
            evs = entry.get("evidence")
            if evs is None:
                errors.append(f"schema_invalid: evidence_map[{i}] missing key 'evidence'")
                continue
            if not isinstance(evs, list):
                errors.append(
                    f"schema_invalid: evidence_map[{i}].evidence must be a list"
                )
                continue
            for j, ev in enumerate(evs):
                if not isinstance(ev, dict):
                    errors.append(
                        f"schema_invalid: evidence_map[{i}].evidence[{j}] must be an object"
                    )
                else:
                    for k in ("source", "location", "quote", "allowed_claims"):
                        if k not in ev:
                            errors.append(
                                f"schema_invalid: evidence_map[{i}].evidence[{j}] "
                                f"missing key {k!r}"
                            )

    # resume_strategy.experience must be a list (deep validators iterate it)
    rs = data.get("resume_strategy")
    if isinstance(rs, dict):
        exp = rs.get("experience")
        if exp is not None and not isinstance(exp, list):
            errors.append(
                f"schema_invalid: resume_strategy.experience must be a list, "
                f"got {type(exp).__name__}"
            )

    # narrative_plan must be a dict
    np = data.get("narrative_plan")
    if "narrative_plan" in data and not isinstance(np, dict):
        errors.append(
            f"schema_invalid: narrative_plan must be an object, "
            f"got {type(np).__name__}"
        )

    # skill_graph must be a dict
    sg = data.get("skill_graph")
    if "skill_graph" in data and not isinstance(sg, dict):
        errors.append(
            f"schema_invalid: skill_graph must be an object, got {type(sg).__name__}"
        )

    return errors[:15]  # cap to avoid flooding repair context


def get_client() -> OpenAI:
    """Return a module-level OpenAI client, creating it on first call."""
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


@dataclass
class TailorResult:
    resume: str | None = None
    cover_letter: str | None = None


# ---------------------------------------------------------------------------
# TailoringPlan validation
# ---------------------------------------------------------------------------

class PlanValidationError(ValueError):
    """Raised when Phase 1 JSON does not match the required schema."""


class PlanParseError(Exception):
    """Raised when a Phase 1 LLM response cannot be parsed as JSON.

    Carries the raw response text so the caller can pass it to the repair
    prompt as RAW_TEXT (repair cannot work with an empty INVALID_PLAN).
    """

    def __init__(self, original_exc: json.JSONDecodeError, raw_content: str) -> None:
        super().__init__(str(original_exc))
        self.raw_content = raw_content


def validate_plan(data: Any) -> dict:
    """Validate and return the plan dict, raising PlanValidationError on failure.

    Checks performed:
    - All required top-level keys are present.
    - role_level is a known value.
    - jd_top_themes has 4-7 entries.
    - Each evidence quote is <= 25 words.
    """
    if not isinstance(data, dict):
        raise PlanValidationError(f"Plan must be a JSON object, got {type(data).__name__}")

    missing = _PLAN_REQUIRED_KEYS - data.keys()
    if missing:
        raise PlanValidationError(f"Plan missing required keys: {missing}")

    # Coerce role_level in place; maps job-title strings to the valid enum.
    if isinstance(data.get("role_level"), str):
        data["role_level"] = _coerce_role_level(data["role_level"])
    role_level = data.get("role_level")
    if role_level not in _VALID_ROLE_LEVELS:
        raise PlanValidationError(
            f"role_level must be one of {_VALID_ROLE_LEVELS}, got {role_level!r}"
        )

    themes = data.get("jd_top_themes", [])
    if not isinstance(themes, list) or not (4 <= len(themes) <= 7):
        raise PlanValidationError(
            f"jd_top_themes must be a list of 4-7 items, got "
            f"{len(themes) if isinstance(themes, list) else type(themes).__name__}"
        )

    for entry in data.get("evidence_map", []):
        for ev in entry.get("evidence", []):
            quote = ev.get("quote", "")
            word_count = len(quote.split())
            if word_count > 25:
                raise PlanValidationError(
                    f"Evidence quote exceeds 25-word limit ({word_count} words): {quote!r}"
                )

    vocab = data.get("vocabulary_anchoring")
    if not isinstance(vocab, dict):
        raise PlanValidationError(
            f"vocabulary_anchoring must be a JSON object, got {type(vocab).__name__}"
        )
    if not isinstance(vocab.get("must_embed"), list):
        raise PlanValidationError("vocabulary_anchoring.must_embed must be a list")
    if not isinstance(vocab.get("optional_embed"), list):
        raise PlanValidationError("vocabulary_anchoring.optional_embed must be a list")

    # Domain fields
    try:
        rules_data = load_domain_translation_rules()
        valid_domains: set[str] = set(rules_data.get("domains", []))
    except Exception:
        valid_domains = set()

    for field in ("jd_domain", "candidate_primary_domain"):
        val = data.get(field)
        if not isinstance(val, str):
            raise PlanValidationError(
                f"{field} must be a string, got {type(val).__name__}"
            )
        if valid_domains and val not in valid_domains:
            raise PlanValidationError(
                f"{field} {val!r} is not a valid domain"
            )

    if not isinstance(data.get("domain_mismatch"), bool):
        raise PlanValidationError(
            f"domain_mismatch must be a boolean, got "
            f"{type(data.get('domain_mismatch')).__name__}"
        )

    rule_ids = data.get("domain_translation_rule_ids")
    if not isinstance(rule_ids, list):
        raise PlanValidationError(
            f"domain_translation_rule_ids must be a list, got {type(rule_ids).__name__}"
        )
    if len(rule_ids) > 3:
        raise PlanValidationError(
            f"domain_translation_rule_ids must have at most 3 entries, got {len(rule_ids)}"
        )

    # narrative_plan: anchor_role_id must match a role in resume_strategy.experience
    np = data.get("narrative_plan")
    if isinstance(np, dict):
        anchor = np.get("anchor_role_id", "")
        if anchor:
            exp_roles = [
                e.get("role_name", "")
                for e in (data.get("resume_strategy") or {}).get("experience", [])
            ]
            if exp_roles and anchor not in exp_roles:
                raise PlanValidationError(
                    f"narrative_plan.anchor_role_id {anchor!r} does not match any "
                    f"resume_strategy.experience role_name: {exp_roles}"
                )

    # skill_graph: basic structural check
    sg = data.get("skill_graph")
    if isinstance(sg, dict):
        if not isinstance(sg.get("direct_skills"), list):
            raise PlanValidationError("skill_graph.direct_skills must be a list")
        if not isinstance(sg.get("related_skills"), list):
            raise PlanValidationError("skill_graph.related_skills must be a list")

    return data


# ---------------------------------------------------------------------------
# Phase 1 — plan_tailoring
# ---------------------------------------------------------------------------

def plan_tailoring(
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[dict, list, dict]:
    """Phase 1: produce a structured TailoringPlan JSON.

    Parameters
    ----------
    job:
        Structured job data (company, title, description).
    resume_template:
        Plain-text content of the resume template.
    cover_template:
        Plain-text content of the cover letter template.

    Returns
    -------
    plan:
        Validated TailoringPlan dict.
    messages:
        Full message list sent to Phase 1 LLM.
    debug_meta:
        Dict with ``model``, ``usage`` (token counts), ``raw_response``.
    """
    planner_instructions = _load_prompt("phase1")
    profile = _load_candidate_profile()

    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    task = _load_prompt(
        "task",
        company=job.company,
        job_title=job.job_title,
        current_date=current_date,
    )

    try:
        domain_rules = load_domain_translation_rules()
        domain_rules_blob = json.dumps(
            {"domains": domain_rules["domains"], "rules": domain_rules["rules"]},
            indent=2,
        )
    except Exception as exc:
        logger.warning("Could not load domain_translation_rules.json: %s", exc)
        domain_rules_blob = ""

    messages: list = [
        {"role": "developer", "content": planner_instructions},
    ]
    if domain_rules_blob:
        messages.append(
            {"role": "user", "content": f"DOMAIN_TRANSLATION_RULES:\n{domain_rules_blob}"}
        )
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    messages.append({"role": "user", "content": task})
    messages.append({"role": "user", "content": f"CURRENT_DATE:\n{current_date}"})

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "tailoring_plan",
            "schema": _get_phase1_schema(),
            "strict": True,
        },
    }
    response = get_client().chat.completions.create(
        model=PHASE1_MODEL,
        messages=messages,
        temperature=PHASE1_TEMPERATURE,
        **_max_tokens_kwargs(PHASE1_MODEL, PHASE1_MAX_TOKENS),
        response_format=response_format,
    )

    raw_content = response.choices[0].message.content
    usage = _extract_usage(response)
    debug_meta = {
        "model": PHASE1_MODEL,
        "usage": usage,
        "raw_response": raw_content,
        "prompt_used": "PLAN",
    }
    try:
        plan_data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise PlanParseError(exc, raw_content) from exc

    llm_request = _build_llm_request_record(
        messages, PHASE1_MODEL, PHASE1_TEMPERATURE, PHASE1_MAX_TOKENS, response_format,
    )
    return plan_data, llm_request, debug_meta


def plan_repair_tailoring(
    invalid_plan: dict,
    schema_errors: list[str],
    validation_errors: list[str],
    job: JobData,
    resume_template: str,
    cover_template: str,
    raw_text: str | None = None,
) -> tuple[dict, list, dict]:
    """Phase 1 repair: rebuild a plan that failed schema or deep validation.

    Parameters
    ----------
    invalid_plan:
        The last parsed plan dict (may be ``{}`` if JSON parsing failed).
    schema_errors:
        Structural errors from ``_run_schema_gate`` (type mismatches, missing keys).
    validation_errors:
        Deep validation errors from ``validate_plan`` / ``validate_plan_extended``.
    job, resume_template, cover_template:
        Same sources used in the original ``plan_tailoring`` call.

    Returns
    -------
    plan:
        Raw (unvalidated) plan dict from the repair LLM call.
    messages:
        Full message list sent to the repair LLM.
    debug_meta:
        Dict with ``model``, ``usage``, ``raw_response``.
    """
    repair_instructions = _load_prompt("phase1_repair")
    profile = _load_candidate_profile()

    try:
        domain_rules = load_domain_translation_rules()
        domain_rules_blob = json.dumps(
            {"domains": domain_rules["domains"], "rules": domain_rules["rules"]},
            indent=2,
        )
    except Exception as exc:
        logger.warning("Could not load domain_translation_rules.json: %s", exc)
        domain_rules_blob = ""

    messages: list = [
        {"role": "developer", "content": repair_instructions},
        {
            "role": "user",
            "content": f"SCHEMA_ERRORS:\n{json.dumps(schema_errors, indent=2)}",
        },
        {
            "role": "user",
            "content": f"VALIDATION_ERRORS:\n{json.dumps(validation_errors, indent=2)}",
        },
        {
            "role": "user",
            "content": f"INVALID_PLAN:\n{json.dumps(invalid_plan, indent=2)}",
        },
    ]
    if raw_text:
        messages.append({"role": "user", "content": f"RAW_TEXT:\n{raw_text}"})
    if domain_rules_blob:
        messages.append(
            {"role": "user", "content": f"DOMAIN_TRANSLATION_RULES:\n{domain_rules_blob}"}
        )
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "tailoring_plan",
            "schema": _get_phase1_schema(),
            "strict": True,
        },
    }
    response = get_client().chat.completions.create(
        model=PHASE1_MODEL,
        messages=messages,
        temperature=PHASE1_REPAIR_TEMPERATURE,
        **_max_tokens_kwargs(PHASE1_MODEL, PHASE1_MAX_TOKENS),
        response_format=response_format,
    )

    raw_content = response.choices[0].message.content
    usage = _extract_usage(response)
    debug_meta = {
        "model": PHASE1_MODEL,
        "usage": usage,
        "raw_response": raw_content,
        "prompt_used": "REPAIR",
    }
    try:
        plan_data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise PlanParseError(exc, raw_content) from exc

    llm_request = _build_llm_request_record(
        messages, PHASE1_MODEL, PHASE1_REPAIR_TEMPERATURE, PHASE1_MAX_TOKENS, response_format,
    )
    return plan_data, llm_request, debug_meta


# ---------------------------------------------------------------------------
# Phase 2 — tailor_documents_with_plan  (writer + validator + repair)
# ---------------------------------------------------------------------------

_PLAN_OBEY_RULES = """
TAILORING_PLAN COMPLIANCE
You will be given a TAILORING_PLAN JSON as a user message.
Follow it exactly: apply its resume_strategy and cover_letter_strategy.
If the plan conflicts with MASTER_RESUME or CANDIDATE_PROFILE, prefer the
source documents and adjust the plan guidance safely -- never invent facts
to satisfy the plan.
Do not add skills, technologies, or claims listed in risk_checks.do_not_invent.
Preserve every metric listed in each role's keep_metrics.
""".strip()

_WRITER_PACKET_RULES = """
WRITER_PACKET COMPLIANCE
You will be given a WRITER_PACKET JSON as the first user message.
It encodes hard constraints derived from the plan and source documents.
You MUST follow all of these:
- must_keep_metrics: every item MUST appear verbatim in the resume output.
- must_surface_arch_mechanisms: at least 4 MUST appear explicitly in the top 2
  roles. Use concrete names such as "read replicas", "horizontal scaling",
  "multi-layer caching", "async messaging", "stateless services", etc.
  Do NOT write "improved scalability" without naming the mechanism.
- must_include_skills: every item MUST be present in the Technical Skills
  section if it exists in the allowed pool.
- do_not_add_terms: NEVER add these to the output.
- unsafe_jd_nouns: NEVER use these unless the exact term is present in
  MASTER_RESUME or CANDIDATE_PROFILE.
- density_targets: the top 2 roles MUST each have 4-6 bullets.
""".strip()


def tailor_documents_with_plan(
    plan: dict,
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[TailorResult, list, dict]:
    """Phase 2: generate + validate + optionally repair tailored documents.

    Flow
    ----
    1. Build WriterPacket from plan + sources.
    2. Run Phase 2 writer LLM call.
    3. Validate output with Phase2Validator.
    4. If validation fails: run one repair pass using phase2_repair.txt.
    5. Return best-effort result plus full debug_meta.

    Returns
    -------
    result:
        TailorResult with resume and cover_letter text.
    first_attempt_messages:
        Message list from the first (writer) LLM call.
    debug_meta:
        Dict containing writer_packet, attempts (per-call details),
        final_validation_ok, model, usage.
    """
    profile_str = _load_candidate_profile()

    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    task = _load_prompt(
        "task",
        company=job.company,
        job_title=job.job_title,
        current_date=current_date,
    )

    writer_packet = build_writer_packet(
        plan, profile_str, resume_template, job.description
    )

    # --- Attempt 1: normal writer ---
    result, attempt1_messages, attempt1_meta = _run_phase2_writer(
        writer_packet, plan, job, resume_template, cover_template,
        profile_str, task, current_date,
    )

    current_ledger: dict | None = attempt1_meta.get("evidence_ledger")
    current_domain_translation_ledger: list | None = attempt1_meta.get("domain_translation_ledger")
    validation1 = validate_phase2_output(
        writer_packet,
        result.resume or "",
        result.cover_letter or "",
        current_date,
        evidence_ledger=current_ledger,
        domain_translation_ledger=current_domain_translation_ledger,
    )

    attempts = [
        {
            "llm_request": attempt1_messages,
            "llm_response_raw": attempt1_meta["raw_response"],
            "validation_report": validation1,
            "model": attempt1_meta["model"],
            "usage": attempt1_meta["usage"],
            "evidence_ledger": current_ledger,
            "domain_translation_ledger": current_domain_translation_ledger,
        }
    ]

    # --- Repair passes: each iteration feeds the previous output as its draft ---
    current_result = result
    current_validation = validation1

    for _repair_idx in range(PHASE2_MAX_REPAIR_ATTEMPTS):
        if current_validation["ok"]:
            break
        repair_result, repair_messages, repair_meta = _run_phase2_repair(
            writer_packet, plan, job, resume_template, cover_template,
            profile_str, task, current_date, current_validation, current_result,
            draft_ledger=current_ledger,
            draft_domain_translation_ledger=current_domain_translation_ledger,
        )
        current_ledger = repair_meta.get("evidence_ledger")
        current_domain_translation_ledger = repair_meta.get("domain_translation_ledger")
        repair_validation = validate_phase2_output(
            writer_packet,
            repair_result.resume or "",
            repair_result.cover_letter or "",
            current_date,
            evidence_ledger=current_ledger,
            domain_translation_ledger=current_domain_translation_ledger,
        )
        attempts.append(
            {
                "llm_request": repair_messages,
                "llm_response_raw": repair_meta["raw_response"],
                "validation_report": repair_validation,
                "model": repair_meta["model"],
                "usage": repair_meta["usage"],
                "evidence_ledger": current_ledger,
                "domain_translation_ledger": current_domain_translation_ledger,
            }
        )
        current_result = repair_result
        current_validation = repair_validation

    final_result = current_result
    final_validation = current_validation

    # --- Judge round: post-repair semantic verification for ledger mismatches ---
    judge_round_meta: dict = {}
    if (
        not final_validation["ok"]
        and ENABLE_PHASE2_JUDGE
        and final_validation.get("judge_candidates")
        and any(
            LEDGER_MISMATCH_ERROR_PREFIX in e
            for e in final_validation.get("errors", [])
        )
    ):
        judge_verdicts, judge_meta = _run_judge_round(
            final_validation["judge_candidates"],
            final_result,
            job,
        )
        judge_round_meta = {"judge_verdicts": judge_verdicts, "judge_meta": judge_meta}
        if judge_verdicts:
            final_validation = apply_judge_to_validation(final_validation, judge_verdicts)
            if final_validation["ok"]:
                logger.info("Phase 2 validation passed after judge round.")

    if not final_validation["ok"]:
        logger.warning(
            "Phase 2 validation still failing after repair loop: %s",
            final_validation["errors"],
        )

    last_attempt = attempts[-1]
    debug_meta: dict = {
        "model": PHASE2_MODEL,
        "usage": last_attempt["usage"],
        "raw_response": last_attempt["llm_response_raw"],
        "writer_packet": writer_packet,
        "attempts": attempts,
        "final_validation_ok": final_validation["ok"],
        "phase2_prompt_name": attempt1_meta.get("phase2_prompt_name", "tailor"),
        "judge_round": judge_round_meta,
    }

    return final_result, attempt1_messages, debug_meta


# ---------------------------------------------------------------------------
# Phase 2 internal helpers
# ---------------------------------------------------------------------------

def _build_phase2_developer_instructions(role_level: str = "senior") -> tuple[str, str]:
    """Build developer instructions for the Phase 2 writer.

    Prefers ``phase2.txt`` — a self-contained plan-aware writer prompt
    that already embeds plan/packet compliance rules.  Falls back to the
    legacy assembly (``tailor.txt`` + inline constants) when the file is absent.

    Parameters
    ----------
    role_level:
        The active role level (from WriterPacket), forwarded to ``role.txt``
        as ``{ROLE_LEVEL}`` if the prompt uses that placeholder.

    Returns
    -------
    instructions:
        Full developer instruction block ready to use as the first message.
    prompt_name:
        ``"phase2"`` or ``"tailor"`` — recorded in debug_meta.
    """
    phase2_base = _load_prompt_optional("phase2").strip()
    if phase2_base:
        prompt_name = "phase2"
        parts: list[str] = [phase2_base]
    else:
        prompt_name = "tailor"
        parts = [
            _load_prompt("tailor").strip(),
            _PLAN_OBEY_RULES,
            _WRITER_PACKET_RULES,
        ]

    # Optional per-candidate / per-role overlays (always appended when present).
    # role.txt receives ROLE_LEVEL so it can highlight the active level.
    parts += [
        _load_prompt_optional("candidate").strip(),
        _load_prompt_optional("role", ROLE_LEVEL=role_level).strip(),
    ]

    return "\n\n".join(p for p in parts if p), prompt_name


def _run_phase2_writer(
    writer_packet: dict,
    plan: dict,
    job: JobData,
    resume_template: str,
    cover_template: str,
    profile_str: str,
    task: str,
    current_date: str,
) -> tuple[TailorResult, list, dict]:
    """Execute a normal Phase 2 writer LLM call."""
    developer_instructions, prompt_name = _build_phase2_developer_instructions(
        role_level=writer_packet.get("role_level", "senior")
    )

    messages: list = [
        {"role": "developer", "content": developer_instructions},
        # WriterPacket first — sets hard constraints before anything else
        {"role": "user", "content": f"WRITER_PACKET:\n{json.dumps(writer_packet, indent=2)}"},
        {"role": "user", "content": f"TAILORING_PLAN:\n{json.dumps(plan, indent=2)}"},
    ]
    if profile_str:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile_str}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    messages.append({"role": "user", "content": task})
    messages.append({"role": "user", "content": f"CURRENT_DATE:\n{current_date}"})

    response_format = {"type": "json_object"}
    response = get_client().chat.completions.create(
        model=PHASE2_MODEL,
        messages=messages,
        temperature=PHASE2_TEMPERATURE,
        **_max_tokens_kwargs(PHASE2_MODEL, PHASE2_MAX_TOKENS),
        response_format=response_format,
    )

    result, _, meta = _parse_phase2_response(response, PHASE2_MODEL)
    meta["phase2_prompt_name"] = prompt_name
    llm_request = _build_llm_request_record(
        messages, PHASE2_MODEL, PHASE2_TEMPERATURE, PHASE2_MAX_TOKENS, response_format,
    )
    return result, llm_request, meta


def _run_phase2_repair(
    writer_packet: dict,
    plan: dict,
    job: JobData,
    resume_template: str,
    cover_template: str,
    profile_str: str,
    task: str,
    current_date: str,
    validation_report: dict,
    draft: TailorResult,
    draft_ledger: dict | None = None,
    draft_domain_translation_ledger: list | None = None,
) -> tuple[TailorResult, list, dict]:
    """Execute a repair pass using the phase2_repair.txt prompt."""
    repair_instructions = _load_prompt("phase2_repair")

    working_output_dict: dict = {
        "resume": draft.resume or "",
        "cover_letter": draft.cover_letter or "",
    }
    if draft_ledger is not None:
        working_output_dict["evidence_ledger"] = draft_ledger
    if draft_domain_translation_ledger is not None:
        working_output_dict["domain_translation_ledger"] = draft_domain_translation_ledger
    working_output = json.dumps(working_output_dict, indent=2)

    validation_errors = validation_report.get("errors", [])
    repair_brief = validation_report.get("repair_brief", {})

    messages: list = [
        {"role": "developer", "content": repair_instructions},
        {"role": "user", "content": f"VALIDATION_ERRORS:\n{json.dumps(validation_errors, indent=2)}"},
        {"role": "user", "content": f"REPAIR_BRIEF:\n{json.dumps(repair_brief, indent=2)}"},
        {"role": "user", "content": f"WRITER_PACKET:\n{json.dumps(writer_packet, indent=2)}"},
        {"role": "user", "content": f"WORKING_OUTPUT_JSON:\n{working_output}"},
        {"role": "user", "content": f"TAILORING_PLAN:\n{json.dumps(plan, indent=2)}"},
    ]
    if profile_str:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile_str}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    messages.append({"role": "user", "content": task})
    messages.append({"role": "user", "content": f"CURRENT_DATE:\n{current_date}"})

    response_format = {"type": "json_object"}
    response = get_client().chat.completions.create(
        model=PHASE2_MODEL,
        messages=messages,
        temperature=PHASE2_REPAIR_TEMPERATURE,
        **_max_tokens_kwargs(PHASE2_MODEL, PHASE2_MAX_TOKENS),
        response_format=response_format,
    )

    result, _, meta = _parse_phase2_response(response, PHASE2_MODEL)
    llm_request = _build_llm_request_record(
        messages, PHASE2_MODEL, PHASE2_REPAIR_TEMPERATURE, PHASE2_MAX_TOKENS, response_format,
    )
    return result, llm_request, meta


def _parse_phase2_response(response: Any, model: str) -> tuple[TailorResult, list, dict]:
    raw_content = response.choices[0].message.content
    raw = json.loads(raw_content)

    def _str_field(*keys: str) -> str | None:
        """Return the first key whose value is a non-empty string; None otherwise.

        Guards against the LLM returning a structured dict for resume/cover_letter
        (e.g. when the response schema grows complex), which would cause downstream
        AttributeError when string methods are called on the value.
        """
        for key in keys:
            val = raw.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    result = TailorResult(
        resume=_str_field("resume", "tailored_resume"),
        cover_letter=_str_field("cover_letter", "tailored_cover_letter"),
    )
    usage = _extract_usage(response)
    meta: dict = {
        "model": model,
        "usage": usage,
        "raw_response": raw_content,
    }
    # Extract evidence_ledger when the LLM included it
    ledger = raw.get("evidence_ledger")
    if isinstance(ledger, dict):
        meta["evidence_ledger"] = ledger
    # Extract domain_translation_ledger when the LLM included it (v8.0+)
    domain_ledger = raw.get("domain_translation_ledger")
    if isinstance(domain_ledger, list):
        meta["domain_translation_ledger"] = domain_ledger
    # messages not available here; callers own their message lists
    return result, [], meta


# ---------------------------------------------------------------------------
# Phase 2 judge round
# ---------------------------------------------------------------------------

def _run_judge_round(
    judge_candidates: list[dict],
    result: TailorResult,
    job: JobData,
) -> tuple[dict[str, bool], dict]:
    """Call the judge model to semantically verify ledger-span mismatches.

    Only called after all writer + repair attempts have been exhausted and
    at least one ``LEDGER_SPAN_MISMATCH`` error remains.

    Parameters
    ----------
    judge_candidates:
        Items from ``ValidationReport.judge_candidates`` — entries where
        honesty check passed but ``exact_span != target``.
    result:
        The final TailorResult (resume + cover_letter text).
    job:
        Job data (used for job description context).

    Returns
    -------
    verdicts:
        ``{candidate_id: True/False}`` — True means semantically approved.
    meta:
        Debug info: model, usage, raw_response.
    """
    judge_instructions = _load_prompt_optional("judge").strip()
    if not judge_instructions:
        logger.warning("prompts/judge.txt not found; skipping judge round.")
        return {}, {}

    # Cap candidates to avoid excessive context usage (spec: up to 20 entries)
    candidates = judge_candidates[:20]

    messages: list = [
        {"role": "developer", "content": judge_instructions},
        {"role": "user", "content": f"TARGETS_TO_CHECK:\n{json.dumps(candidates, indent=2)}"},
        {"role": "user", "content": f"RESUME_TEXT:\n{result.resume or ''}"},
        {"role": "user", "content": f"COVER_LETTER_TEXT:\n{result.cover_letter or ''}"},
        {"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"},
    ]

    response = get_client().chat.completions.create(
        model=PHASE2_JUDGE_MODEL,
        messages=messages,
        temperature=0.0,
        response_format={"type": "json_object"},
    )

    raw_content = response.choices[0].message.content
    usage = _extract_usage(response)
    meta = {
        "model": PHASE2_JUDGE_MODEL,
        "usage": usage,
        "raw_response": raw_content,
    }

    try:
        raw = json.loads(raw_content)
        results_list = raw.get("results", [])
        verdicts: dict[str, bool] = {
            r["id"]: r.get("verdict", "no") == "yes"
            for r in results_list
            if isinstance(r, dict) and "id" in r
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Judge response could not be parsed (%s); all rejected.", exc)
        verdicts = {}

    logger.info(
        "Judge round: %d candidate(s) evaluated, %d approved.",
        len(candidates),
        sum(1 for v in verdicts.values() if v),
    )
    return verdicts, meta


# ---------------------------------------------------------------------------
# Single-pass fallback (original behaviour)
# ---------------------------------------------------------------------------

def tailor_documents(
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[TailorResult, list]:
    """Single-pass generate a tailored resume and cover letter (fallback).

    Returns
    -------
    result:
        TailorResult with ``resume`` and ``cover_letter`` text fields.
    messages:
        The full message list sent to the LLM (useful for debug logging).
    """
    developer_instructions = "\n\n".join(
        part for part in (
            _load_prompt("tailor").strip(),
            _load_prompt_optional("candidate").strip(),
            _load_prompt_optional("role").strip(),
        )
        if part
    )
    profile = _load_candidate_profile()

    messages: list = [
        {"role": "developer", "content": developer_instructions},
    ]
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    task = _load_prompt(
        "task",
        company=job.company,
        job_title=job.job_title,
        current_date=current_date,
    )
    messages.append({"role": "user", "content": task})

    response_format = {"type": "json_object"}
    response = get_client().chat.completions.create(
        model=SIMPLE_MODEL,
        messages=messages,
        temperature=SIMPLE_TEMPERATURE,
        response_format=response_format,
    )

    raw = json.loads(response.choices[0].message.content)
    result = TailorResult(
        resume=raw.get("resume") or raw.get("tailored_resume"),
        cover_letter=raw.get("cover_letter") or raw.get("tailored_cover_letter"),
    )
    llm_request = _build_llm_request_record(
        messages, SIMPLE_MODEL, SIMPLE_TEMPERATURE, response_format=response_format,
    )
    return result, llm_request


# ---------------------------------------------------------------------------
# Metadata extraction (unchanged)
# ---------------------------------------------------------------------------

def extract_metadata_ai(job_text: str) -> dict:
    """Use the LLM to extract company name and job title from raw job text."""
    prompt = _load_prompt("extract_metadata", job_text=job_text)

    response = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _extract_usage(response: Any) -> dict:
    if response.usage:
        return {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
    return {}


_USES_MAX_COMPLETION_TOKENS_RE = re.compile(r"^(o\d|gpt-[5-9])", re.IGNORECASE)


def _max_tokens_kwargs(model: str, max_tokens: int | None) -> dict:
    """Return the correct token-limit kwarg for the given model.

    Newer OpenAI models (o-series, gpt-5+) require ``max_completion_tokens``;
    older models use ``max_tokens``.
    """
    if max_tokens is None:
        return {}
    key = "max_completion_tokens" if _USES_MAX_COMPLETION_TOKENS_RE.match(model) else "max_tokens"
    return {key: max_tokens}


def _build_llm_request_record(
    messages: list,
    model: str,
    temperature: float,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> dict:
    """Package all OpenAI call parameters into a structured debug record."""
    record: dict = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    if max_tokens is not None:
        record.update(_max_tokens_kwargs(model, max_tokens))
    if response_format is not None:
        record["response_format"] = response_format
    return record
