"""Delivery forms of the SMF blob (spec blob-format 2).

    B        20-byte header (u32 words: version, word 2, 1, 5, length) + LZNT1    UEFI variable and EFI capsules
    A        28-byte header (u32 word 6 = length) + UTF-8 text with BOM             Laptop 3 AMD (core 2.51)
    C        raw LZNT1 without header                                               driver-package files (smfblobpackage)
    text     already decoded text                                                   tests, extracted files
    capsule  a Microsoft firmware capsule that contains a form B or A blob
"""
import struct
from dataclasses import dataclass

from . import lznt1
from .errors import BlobError

BOM = b"\xef\xbb\xbf"
FORM_B_MARK = struct.pack("<II", 1, 5)  # words 2 and 3 of the form B header


@dataclass(frozen=True)
class Decoded:
    form: str  # "A", "B", "C", "text", "capsule/A", "capsule/B"
    text: str


def _to_text(raw: bytes) -> str:
    if raw.startswith(BOM):
        raw = raw[len(BOM):]
    if b"\x00" in raw:
        raise BlobError("decoded blob contains NUL bytes")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError as e:
        raise BlobError(f"decoded blob is not ASCII text (offset {e.start})") from None


def _plausible_text(raw: bytes) -> bool:
    """Decoded blobs are CSV: they contain commas and start with a GUID, a digit, a comment or a macro."""
    head = raw[len(BOM):] if raw.startswith(BOM) else raw
    head = head[:200]
    return b"," in head and b"\x00" not in head and all(32 <= c < 127 or c in (9, 10, 13) for c in head)


def _form_b(data: bytes) -> str | None:
    if len(data) < 20:
        return None
    w = struct.unpack_from("<5I", data, 0)
    if (w[2], w[3]) != (1, 5):
        return None
    if w[4] != len(data) - 20:
        raise BlobError(f"form B header: length field {w[4]} does not match the {len(data) - 20} bytes after the header")
    return _to_text(lznt1.decompress(data[20:]))


def _form_a(data: bytes) -> str | None:
    if len(data) < 28 + len(BOM) or data[28:28 + len(BOM)] != BOM:
        return None
    length = struct.unpack_from("<I", data, 24)[0]
    if length != len(data) - 28:
        raise BlobError(f"form A header: length field {length} does not match the {len(data) - 28} bytes after the header")
    return _to_text(data[28:])


def _form_c(data: bytes) -> str | None:
    if not lznt1.looks_like_stream(data):
        return None
    try:
        raw = lznt1.decompress(data)
    except BlobError:
        return None
    return _to_text(raw) if _plausible_text(raw) else None


def _capsule(data: bytes) -> Decoded | None:
    """Search a firmware capsule for an embedded blob. The first candidate that decodes to text wins."""
    i = data.find(FORM_B_MARK)
    while i != -1:
        start = i - 8
        if start >= 0 and i + 12 <= len(data):
            length = struct.unpack_from("<I", data, i + 8)[0]
            end = i + 12 + length
            if 0 < length and end <= len(data):
                try:
                    raw = lznt1.decompress(data[i + 12:end])
                    if _plausible_text(raw):
                        return Decoded("capsule/B", _to_text(raw))
                except BlobError:
                    pass
        i = data.find(FORM_B_MARK, i + 1)
    j = data.find(BOM)
    while j != -1:
        if j >= 28:
            length = struct.unpack_from("<I", data, j - 4)[0]
            if 0 < length <= len(data) - j and _plausible_text(data[j:j + length]):
                return Decoded("capsule/A", _to_text(data[j:j + length]))
        j = data.find(BOM, j + 1)
    return None


def decode(data: bytes, efivar: bool = False) -> Decoded:
    """Detect the delivery form and return the decoded text. `efivar=True` strips the 4 bytes of EFI attributes first."""
    if efivar:
        if len(data) < 4:
            raise BlobError("UEFI variable is empty")
        data = data[4:]
    if not data:
        raise BlobError("blob is empty")
    text = _form_b(data)
    if text is not None:
        return Decoded("B", text)
    text = _form_a(data)
    if text is not None:
        return Decoded("A", text)
    text = _form_c(data)
    if text is not None:
        return Decoded("C", text)
    if _plausible_text(data):
        return Decoded("text", _to_text(data))
    found = _capsule(data)
    if found is not None:
        return found
    raise BlobError("unknown blob format")
