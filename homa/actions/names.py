"""Windows file-name rules for Rename (AC-AC-4). ``validate`` returns None for a usable name, else the reason."""
from typing import Optional

MAX_NAME = 255
_FORBIDDEN = '<>:"/\\|?*'
_RESERVED = frozenset(
    ["con", "prn", "aux", "nul", "conin$", "conout$"]
    + [f"com{c}" for c in "123456789\u00b9\u00b2\u00b3"]
    + [f"lpt{c}" for c in "123456789\u00b9\u00b2\u00b3"]
)


def validate(name: object) -> Optional[str]:
    if not isinstance(name, str) or not name:
        return "The name is empty."
    if not name.strip():
        return "The name is only spaces."
    if len(name) > MAX_NAME:
        return f"The name is {len(name)} characters long; Windows allows at most {MAX_NAME}."
    for ch in name:
        if ord(ch) < 32 or ord(ch) == 127:
            return f"The name contains a control character (code {ord(ch)})."
    bad = sorted({ch for ch in name if ch in _FORBIDDEN})
    if bad:
        return "The name contains a character Windows does not allow: " + " ".join(bad)
    if name[-1] in ". ":
        return "The name ends with a space or a period, which Windows removes."
    stem = name.split(".", 1)[0].rstrip(" ")
    if stem.casefold() in _RESERVED:
        return f'"{stem}" is a reserved Windows device name, with or without an extension.'
    return None
