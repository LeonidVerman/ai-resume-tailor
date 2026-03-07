import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project root is three levels up from src/tailor/config.py
BASE_DIR = Path(__file__).resolve().parent.parent.parent

PROMPTS_DIR = BASE_DIR / "prompts"
SCHEMAS_DIR = BASE_DIR / "schemas"
CONFIG_DIR = BASE_DIR / "config"
PROFILE_DIR = BASE_DIR / "profile"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
TMP_DIR = BASE_DIR / "tmp"


# ---------------------------------------------------------------------------
# Domain translation rules loader
# ---------------------------------------------------------------------------

_DOMAIN_RULES_CACHE: dict | None = None


def load_domain_translation_rules() -> dict:
    """Load and validate config/domain_translation_rules.json.

    Returns the parsed dict augmented with a ``_index`` key mapping
    each ``rule_id`` to its rule object for O(1) lookup.

    Caches the result in memory; subsequent calls are free.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist.
    ValueError
        If required top-level keys (``version``, ``domains``, ``rules``) are absent
        or have wrong types.
    """
    global _DOMAIN_RULES_CACHE
    if _DOMAIN_RULES_CACHE is not None:
        return _DOMAIN_RULES_CACHE

    path = CONFIG_DIR / "domain_translation_rules.json"
    with open(path, encoding="utf-8") as f:
        data: dict = json.load(f)

    for key in ("version", "domains", "rules"):
        if key not in data:
            raise ValueError(
                f"domain_translation_rules.json missing required key: {key!r}"
            )
    if not isinstance(data["domains"], list):
        raise ValueError("domain_translation_rules.json: 'domains' must be a list")
    if not isinstance(data["rules"], list):
        raise ValueError("domain_translation_rules.json: 'rules' must be a list")

    data["_index"] = {
        rule["rule_id"]: rule
        for rule in data["rules"]
        if isinstance(rule, dict) and "rule_id" in rule
    }

    _DOMAIN_RULES_CACHE = data
    return data

RESUME_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Resume_Template.docx")
COVER_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Cover_Letter_Template.docx")

DOCKER_IMAGE_DEFAULT = "minidocks/libreoffice"

# --- Two-phase tailoring configuration ---
ENABLE_TWO_PHASE: bool = os.environ.get("ENABLE_TWO_PHASE", "true").lower() not in ("0", "false", "no")
ENABLE_PLAN_REPAIR: bool = os.environ.get("ENABLE_PLAN_REPAIR", "true").lower() not in ("0", "false", "no")

PHASE1_MODEL: str = os.environ.get("PHASE1_MODEL", "gpt-5.2")
PHASE2_MODEL: str = os.environ.get("PHASE2_MODEL", "gpt-5.2")

PHASE1_TEMPERATURE: float = float(os.environ.get("PHASE1_TEMPERATURE", "0.1"))
PHASE1_REPAIR_TEMPERATURE: float = float(os.environ.get("PHASE1_REPAIR_TEMPERATURE", "0.1"))
PHASE2_TEMPERATURE: float = float(os.environ.get("PHASE2_TEMPERATURE", "0.3"))
PHASE2_REPAIR_TEMPERATURE: float = float(os.environ.get("PHASE2_REPAIR_TEMPERATURE", "0.05"))

PHASE1_MAX_TOKENS: int = int(os.environ.get("PHASE1_MAX_TOKENS", "8000"))
PHASE2_MAX_TOKENS: int = int(os.environ.get("PHASE2_MAX_TOKENS", "8000"))
PHASE2_MAX_REPAIR_ATTEMPTS: int = int(os.environ.get("PHASE2_MAX_REPAIR_ATTEMPTS", "1"))

# --- Phase 2 judge model (post-repair semantic verification) ---
PHASE2_JUDGE_MODEL: str = os.environ.get("PHASE2_JUDGE_MODEL", "gpt-4o-mini")
ENABLE_PHASE2_JUDGE: bool = os.environ.get("ENABLE_PHASE2_JUDGE", "true").lower() not in ("0", "false", "no")

# --- Single-pass (--simple) mode ---
SIMPLE_MODEL: str = os.environ.get("SIMPLE_MODEL", "gpt-5.2")
SIMPLE_TEMPERATURE: float = float(os.environ.get("SIMPLE_TEMPERATURE", "0.3"))

# --- Assessment mode ---
ASSESS_MODEL: str = os.environ.get("ASSESS_MODEL", "gpt-4o-mini")
ASSESS_TEMPERATURE: float = float(os.environ.get("ASSESS_TEMPERATURE", "0.2"))
