import re

from tailor.config import PROFILE_DIR, PROMPTS_DIR


def _read_text_file(path):
    """Read a text file, trying encodings from most to least strict.

    Encoding priority:
      1. utf-8-sig  — UTF-8 with or without BOM (the common cross-platform case)
      2. cp1252     — Windows-1252 (Windows default; covers 0x80-0x9F like en-dash 0x96)
      3. latin-1    — ISO-8859-1; every byte is valid, so this never raises

    This means a Windows-saved file with smart quotes or dashes (cp1252)
    is read correctly rather than crashing with UnicodeDecodeError.
    """
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    # Should never be reached (latin-1 accepts all bytes), but be safe
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _load_prompt(name, **kwargs):
    """Load prompts/<name>.txt and substitute {placeholder} values.

    Only tokens of the form ``{word}`` whose name appears in *kwargs* are
    replaced.  All other brace sequences (e.g. JSON examples in the prompt)
    are left exactly as written, so prompt files can contain literal JSON
    without any escaping.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    try:
        template = _read_text_file(path)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            "Create the file or check the prompts/ directory."
        )
    return re.sub(
        r"\{(\w+)\}",
        lambda m: str(kwargs[m.group(1)]) if m.group(1) in kwargs else m.group(0),
        template,
    )


def _load_prompt_optional(name, **kwargs):
    """Like _load_prompt but returns '' if the file is missing or empty."""
    try:
        return _load_prompt(name, **kwargs)
    except FileNotFoundError:
        return ""


def _load_candidate_profile(profile_text: str | None = None) -> str:
    """Return the candidate profile string for injection into LLM messages.

    If *profile_text* is provided (e.g. serialized from a DB-backed profile),
    it is returned as-is.  Otherwise falls back to reading
    profile/candidate_profile.json (CLI behaviour).

    Returns an empty string if no profile is available so callers degrade
    gracefully rather than crashing.
    """
    if profile_text is not None:
        return profile_text
    profile_path = PROFILE_DIR / "candidate_profile.json"
    try:
        return _read_text_file(profile_path)
    except FileNotFoundError:
        return ""
