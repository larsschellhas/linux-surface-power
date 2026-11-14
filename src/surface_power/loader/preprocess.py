"""Preprocessor for the newer text forms (spec blob-format 4.1).

Handles line comments `//`, macro definitions `#macro NAME VALUE`, references `%NAME%` and the built-in macro `VN<n>`.
Braced channels `{GUID}_N` are split into fields by the parser, because they may also occur inside expressions.
Plain CSV blobs pass through unchanged. Line numbers of the original text are kept for error messages.
"""
import re
from dataclasses import dataclass

from .errors import BlobError

INTERNAL_NODE_GUID = "FB805E77-616E-4EA8-BAC1-6859A6C2D4B1"
MAX_NAME = 63      # longest macro name the Windows core accepts [C]
MAX_DEPTH = 16     # nesting limit for macro references [D]

_REF = re.compile(r"%([A-Za-z0-9_]+)%")
_VN = re.compile(r"VN(\d+)")


@dataclass(frozen=True)
class Line:
    number: int  # 1-based line number in the decoded text
    text: str


def _expand(text: str, macros: dict[str, str], number: int) -> str:
    def replace(m: re.Match) -> str:
        name = m.group(1)
        if name in macros:
            return macros[name]
        vn = _VN.fullmatch(name)
        if vn:
            return f"{{{INTERNAL_NODE_GUID}}}_{vn.group(1)}"
        raise BlobError(f"undefined macro %{name}%", number)

    for _ in range(MAX_DEPTH):
        expanded = _REF.sub(replace, text)
        if expanded == text:
            return text
        text = expanded
    raise BlobError(f"macro references nested deeper than {MAX_DEPTH} levels", number)


def run(text: str) -> list[Line]:
    """Return the preprocessed lines. Macro definition lines are dropped; all other lines are kept, so numbers stay meaningful."""
    macros: dict[str, str] = {}
    body: list[Line] = []
    for number, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), start=1):
        if "//" in raw:
            raw = raw[:raw.index("//")]
        stripped = raw.strip().lstrip("﻿")
        if stripped.startswith("#macro"):
            parts = stripped.split(None, 2)
            if len(parts) != 3:
                raise BlobError("macro definition needs a name and a value", number)
            name, value = parts[1], parts[2].strip()
            if len(name) > MAX_NAME:
                raise BlobError(f"macro name longer than {MAX_NAME} characters", number)
            macros[name] = value
            continue
        body.append(Line(number, stripped))
    # Expand only after all definitions are known.
    out = []
    for line in body:
        text_ = _expand(line.text, macros, line.number) if "%" in line.text else line.text
        out.append(Line(line.number, text_))
    # Drop trailing empty lines at the end of the text (from the final newline).
    while out and not out[-1].text:
        out.pop()
    return out
