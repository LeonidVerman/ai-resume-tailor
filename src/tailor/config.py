import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project root is three levels up from src/tailor/config.py
BASE_DIR = Path(__file__).resolve().parent.parent.parent

PROMPTS_DIR = BASE_DIR / "prompts"
SCHEMAS_DIR = BASE_DIR / "schemas"
PROFILE_DIR = BASE_DIR / "profile"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
TMP_DIR = BASE_DIR / "tmp"

RESUME_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Resume_Template.docx")
COVER_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Cover_Letter_Template.docx")

DOCKER_IMAGE_DEFAULT = "minidocks/libreoffice"

# --- Two-phase tailoring configuration ---
ENABLE_TWO_PHASE: bool = os.environ.get("ENABLE_TWO_PHASE", "true").lower() not in ("0", "false", "no")
ENABLE_PLAN_REPAIR: bool = os.environ.get("ENABLE_PLAN_REPAIR", "true").lower() not in ("0", "false", "no")

PHASE1_MODEL: str = os.environ.get("PHASE1_MODEL", "gpt-4.1-mini")
PHASE2_MODEL: str = os.environ.get("PHASE2_MODEL", "gpt-4o-mini")

PHASE1_TEMPERATURE: float = float(os.environ.get("PHASE1_TEMPERATURE", "0.1"))
PHASE1_REPAIR_TEMPERATURE: float = float(os.environ.get("PHASE1_REPAIR_TEMPERATURE", "0.1"))
PHASE2_TEMPERATURE: float = float(os.environ.get("PHASE2_TEMPERATURE", "0.3"))
PHASE2_REPAIR_TEMPERATURE: float = float(os.environ.get("PHASE2_REPAIR_TEMPERATURE", "0.05"))

PHASE1_MAX_TOKENS: int = int(os.environ.get("PHASE1_MAX_TOKENS", "8000"))
PHASE2_MAX_TOKENS: int = int(os.environ.get("PHASE2_MAX_TOKENS", "8000"))
PHASE2_MAX_REPAIR_ATTEMPTS: int = int(os.environ.get("PHASE2_MAX_REPAIR_ATTEMPTS", "2"))
