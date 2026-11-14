"""Number parsing as the Windows core does it (spec blob-format 4.3)."""
import re

_INT = re.compile(r"\s*([+-]?\d+)")
_REAL = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*")


def atoi(text: str) -> int:
    """Like C atoi: optional sign and digits; anything after the digits is ignored. No digits at all is an error [D]."""
    m = _INT.match(text)
    if not m:
        raise ValueError(f"not an integer: {text!r}")
    return int(m.group(1))


def real(text: str) -> float:
    """Decimal number with optional sign and fraction. Exponents and other characters are errors."""
    if not _REAL.fullmatch(text):
        raise ValueError(f"not a number: {text!r}")
    return float(text)
