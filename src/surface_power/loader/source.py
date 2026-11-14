"""Where the blob comes from (spec blob-format 1) and the one-call loader used by the daemon and the tools."""
import os
from dataclasses import dataclass

from . import container, parser
from .errors import BlobError
from .model import ParsedBlob

EFIVAR = "/sys/firmware/efi/efivars/SMF_DATA_BLOB-a3d05fa9-db5e-490c-956e-a57384a4b70b"


class NoBlob(Exception):
    """No blob at the given place (FR-D2). Not an error in the blob itself."""


@dataclass(frozen=True)
class Loaded:
    source: str
    form: str
    size: int          # decoded text size in bytes
    parsed: ParsedBlob


def read_raw(path: str | None = None) -> tuple[bytes, str, bool]:
    """Return (data, source, is_efivar). Without a path, the UEFI variable is read."""
    source = path or EFIVAR
    try:
        with open(source, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        raise NoBlob(f"no blob at {source}") from None
    except PermissionError as e:
        raise NoBlob(f"cannot read {source}: {e.strerror}") from None
    efivar = path is None or os.path.basename(source).startswith("SMF_DATA_BLOB-") or source.endswith(".efivar")
    if efivar and len(data) <= 4:
        raise NoBlob(f"UEFI variable {source} is empty")
    return data, source, efivar


def load(path: str | None = None) -> Loaded:
    """Read, decode and parse a blob. Raises NoBlob or BlobError."""
    data, source, efivar = read_raw(path)
    dec = container.decode(data, efivar=efivar)
    return Loaded(source, dec.form, len(dec.text), parser.parse_text(dec.text, dec.form))


__all__ = ["BlobError", "EFIVAR", "Loaded", "NoBlob", "load", "read_raw"]
