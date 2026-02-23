from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Project root is three levels up from src/tailor/config.py
BASE_DIR = Path(__file__).resolve().parent.parent.parent

PROMPTS_DIR = BASE_DIR / "prompts"
PROFILE_DIR = BASE_DIR / "profile"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
TMP_DIR = BASE_DIR / "tmp"

RESUME_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Resume_Template.docx")
COVER_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Cover_Letter_Template.docx")

DOCKER_IMAGE_DEFAULT = "minidocks/libreoffice"
