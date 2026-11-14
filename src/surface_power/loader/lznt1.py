"""LZNT1 decompression as specified in MS-XCA (spec blob-format 3).

The stream is a sequence of chunks. Each chunk starts with a 16-bit header:
    bits 0-11   size of the chunk data minus 1 (without the 2 header bytes)
    bits 12-14  signature, always 0b011
    bit 15      1 = compressed, 0 = stored
A header of 0 ends the stream.

A compressed chunk is a sequence of groups: one flag byte, then up to 8 items. Bit i of the flag byte says whether item i is a
literal byte (0) or a 16-bit copy token (1). The split of the token into displacement and length depends on how many bytes of this
chunk are already decompressed.
"""
import struct

from .errors import BlobError

# The SB3 core decompresses into a buffer of 30 720 bytes [C]; newer blobs are larger (up to ~33 kB). We allow 1 MiB [D].
DEFAULT_LIMIT = 1 << 20


def _displacement_bits(pos: int) -> int:
    """Number of displacement bits for a copy token at position `pos` within the chunk (at least 4)."""
    bits = 4
    while (1 << bits) < pos:
        bits += 1
    return bits


def _decompress_chunk(chunk: bytes, out: bytearray) -> None:
    start = len(out)
    j = 0
    while j < len(chunk):
        flags = chunk[j]
        j += 1
        for bit in range(8):
            if j >= len(chunk):
                return
            if not (flags >> bit) & 1:
                out.append(chunk[j])
                j += 1
                continue
            if j + 2 > len(chunk):
                raise BlobError("LZNT1: copy token cut off")
            token = struct.unpack_from("<H", chunk, j)[0]
            j += 2
            pos = len(out) - start
            bits = _displacement_bits(pos)
            disp = (token >> (16 - bits)) + 1
            length = (token & ((1 << (16 - bits)) - 1)) + 3
            if disp > pos:
                raise BlobError("LZNT1: copy token points before the start of the chunk")
            for _ in range(length):  # byte by byte, because source and target may overlap
                out.append(out[-disp])


def decompress(data: bytes, limit: int = DEFAULT_LIMIT) -> bytes:
    """Decompress an LZNT1 stream. Raises BlobError on malformed data or when the output exceeds `limit` bytes."""
    out = bytearray()
    i = 0
    while i + 2 <= len(data):
        header = struct.unpack_from("<H", data, i)[0]
        if header == 0:
            break
        if (header >> 12) & 0x7 != 0b011:
            raise BlobError(f"LZNT1: bad chunk signature at offset {i}")
        size = (header & 0xFFF) + 1
        chunk = data[i + 2:i + 2 + size]
        if len(chunk) < size:
            raise BlobError(f"LZNT1: chunk at offset {i} is cut off ({len(chunk)} of {size} bytes)")
        i += 2 + size
        if header & 0x8000:
            _decompress_chunk(chunk, out)
        else:
            out += chunk
        if len(out) > limit:
            raise BlobError(f"LZNT1: output larger than {limit} bytes")
    return bytes(out)


def looks_like_stream(data: bytes) -> bool:
    """True if `data` starts with a plausible LZNT1 chunk header."""
    if len(data) < 3:
        return False
    header = struct.unpack_from("<H", data, 0)[0]
    return header != 0 and (header >> 12) & 0x7 == 0b011
