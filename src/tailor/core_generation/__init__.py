"""Core generation module.

Contains the implementation of the document tailoring pipeline:
  - LLM orchestration (plan_tailoring, tailor_documents_with_plan, tailor_documents)
  - Phase 1 plan validation (validate_plan, validate_plan_extended)
  - Phase 2 WriterPacket construction (build_writer_packet)
  - Phase 2 output validation (validate_phase2_output)
  - Mechanism phrase taxonomy (classify_phrase, build_mechanism_taxonomy)

Public API — import from here for new SaaS integration code:

    from tailor.core_generation import (
        plan_tailoring, tailor_documents_with_plan, tailor_documents,
        validate_plan, validate_plan_extended,
        build_writer_packet, validate_phase2_output,
        TailorResult, PlanParseError, PlanValidationError,
    )

Existing callers that import directly from tailor.llm, tailor.writer_packet, etc.
continue to work unchanged via backward-compatible shim modules.
"""

from .llm import (
    TailorResult,
    PlanParseError,
    PlanValidationError,
    get_client,
    extract_metadata_ai,
    plan_tailoring,
    plan_repair_tailoring,
    tailor_documents,
    tailor_documents_with_plan,
    validate_plan,
)
from .plan_validator import validate_plan_extended
from .writer_packet import build_writer_packet
from .phase2_validator import (
    validate_phase2_output,
    apply_judge_to_validation,
)
from .mechanism_taxonomy import (
    PhraseCategory,
    classify_phrase,
    build_mechanism_taxonomy,
)

__all__ = [
    # LLM / orchestration
    "TailorResult",
    "PlanParseError",
    "PlanValidationError",
    "get_client",
    "extract_metadata_ai",
    "plan_tailoring",
    "plan_repair_tailoring",
    "tailor_documents",
    "tailor_documents_with_plan",
    "validate_plan",
    # Phase 1 validation
    "validate_plan_extended",
    # Phase 2 constraint builder
    "build_writer_packet",
    # Phase 2 output validation
    "validate_phase2_output",
    "apply_judge_to_validation",
    # Mechanism taxonomy
    "PhraseCategory",
    "classify_phrase",
    "build_mechanism_taxonomy",
]
