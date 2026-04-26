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

RESUME_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Resume_Template.docx")
COVER_TEMPLATE = str(TEMPLATES_DIR / "Leonid_Verman_Cover_Letter_Template.docx")

DOCKER_IMAGE_DEFAULT = "minidocks/libreoffice"

# --- Generation model ---
SIMPLE_MODEL: str = os.environ.get("SIMPLE_MODEL", "gpt-5.2")
SIMPLE_TEMPERATURE: float = float(os.environ.get("SIMPLE_TEMPERATURE", "0.3"))

# --- Assessment mode ---
ASSESS_MODEL: str = os.environ.get("ASSESS_MODEL", "gpt-4o-mini")
ASSESS_TEMPERATURE: float = float(os.environ.get("ASSESS_TEMPERATURE", "0.2"))

# --- Layout tree serialization ---
# When True, parse_docx captures every w:p / w:tbl as an XML string in
# ResumeDocument.layout_blocks.  This allows the renderer to faithfully
# reproduce DOCX layout (fonts, styles, tables, column structure) after a
# DB round-trip without requiring the original template file at render time.
USE_SERIALIZED_LAYOUT_TREE: bool = (
    os.environ.get("USE_SERIALIZED_LAYOUT_TREE", "true").lower() == "true"
)

# When True, render_docx always uses the layout_blocks path for DOCX sources
# that carry layout_blocks — even when runtime xml_proto objects exist.
# This enforces physical layout order (layout_blocks order == original document
# order) and preserves column/table structure after apply_tailored.
#
# When False (default), the layout_blocks path activates only when xml_proto
# is absent (deserialized from DB) to avoid suppressing LLM-added content.
# LLM-added paragraphs with empty para_id are not placed in layout_blocks
# mode; they are only logged as LAYOUT_UNBOUND_CONTENT_NOT_RENDERED.
USE_LAYOUT_BLOCK_RENDERER: bool = (
    os.environ.get("USE_LAYOUT_BLOCK_RENDERER", "false").lower() == "true"
)
