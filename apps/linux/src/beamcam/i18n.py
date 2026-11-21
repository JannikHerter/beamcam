"""Translations for the window.

Catalogs are standard gettext .po files in `locale/`, shipped inside the package and read at
startup, so a pip install and the distro packages behave alike without a compile step. The
command line stays in English: scripts read its output.
"""

from __future__ import annotations

import ast
import os
from importlib import resources

# Each language's name in that language, as the language menu shows it.
LANGUAGES = {"en": "English", "de": "Deutsch"}

_catalog: dict[str, str] = {}


def _(text: str) -> str:
    return _catalog.get(text) or text


def N_(text: str) -> str:
    """Marks text for translation where it is defined; translate it with _() where it is shown."""
    return text


def parse_po(text: str) -> dict[str, str]:
    """msgid → msgstr for every translated, non-fuzzy entry."""
    entries: dict[str, str] = {}
    msgid: str | None = None
    msgstr: str | None = None
    field: str | None = None
    fuzzy = False  # the current entry
    next_fuzzy = False  # a "#, fuzzy" flag seen for the entry that starts next

    def flush() -> None:
        nonlocal msgid, msgstr, field
        if msgid and msgstr and not fuzzy:
            entries[msgid] = msgstr
        msgid = msgstr = field = None

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#,"):
            next_fuzzy = "fuzzy" in line
        elif not line or line.startswith("#"):
            continue
        elif line.startswith("msgid "):
            flush()
            fuzzy, next_fuzzy = next_fuzzy, False
            msgid, field = ast.literal_eval(line[6:]), "id"
        elif line.startswith("msgstr "):
            msgstr, field = ast.literal_eval(line[7:]), "str"
        elif line.startswith('"'):
            # .po strings use C escapes, which Python string literals understand.
            if field == "id" and msgid is not None:
                msgid += ast.literal_eval(line)
            elif field == "str" and msgstr is not None:
                msgstr += ast.literal_eval(line)
    flush()
    return entries


# LANGUAGE as the user's session set it, saved before a chosen language overrides it. Kept in
# the environment so it survives the restart that switches languages.
_ORIGINAL = "BEAMCAM_ORIGINAL_LANGUAGE"


def system_language() -> str:
    """The first supported language in the environment, as gettext would look for it."""
    env = dict(os.environ)
    if _ORIGINAL in env:
        env["LANGUAGE"] = env[_ORIGINAL]
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        for part in env.get(variable, "").split(":"):
            code = part.split(".")[0].split("@")[0].split("_")[0].lower()
            if code in LANGUAGES:
                return code
    return "en"


def setup(language: str | None) -> str:
    """Loads the catalog for [language], or for the system language when None. Call before GTK
    starts: a chosen language is also passed on to GTK's own texts. Returns the language used."""
    global _catalog
    if language in LANGUAGES:
        code = language
        os.environ.setdefault(_ORIGINAL, os.environ.get("LANGUAGE", ""))
        os.environ["LANGUAGE"] = code
    else:
        code = system_language()
        original = os.environ.pop(_ORIGINAL, None)
        if original is not None:
            if original:
                os.environ["LANGUAGE"] = original
            else:
                os.environ.pop("LANGUAGE", None)
    path = resources.files("beamcam").joinpath(f"locale/{code}.po")
    try:
        _catalog = parse_po(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, SyntaxError):
        _catalog = {}
    return code
