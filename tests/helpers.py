"""Test helpers: a minimal LZNT1 compressor and a builder for synthetic SMF blobs.

The blobs built here are synthetic. They use the public interface identifiers (GUIDs) and made-up parameter values; they are not
copies of any device blob (NFR-8).
"""
import os
import struct
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from surface_power.engine import registry  # noqa: E402

# Interface GUIDs used in synthetic blobs.
VN = "FB805E77-616E-4EA8-BAC1-6859A6C2D4B1"
TEMP = "2E8B14C2-26D6-4119-A62E-9D108C4DDC2A"
SYSTEM = "2DD6960F-7CB9-4BB8-82BF-97FA5D8BD767"
POWER = "28B7BD85-B4CD-49F1-B02B-F84E5AA09889"
LIMITS = "0B7B5350-BB8C-4459-8D4A-A75C57073801"


def lznt1_compress(data: bytes) -> bytes:
    """Greedy LZNT1 compressor. Slow, but good enough for test data of a few kB."""
    out = bytearray()
    for start in range(0, len(data), 4096):
        chunk = data[start:start + 4096]
        body = bytearray()
        i = 0
        while i < len(chunk):
            flags = 0
            group = bytearray()
            for bit in range(8):
                if i >= len(chunk):
                    break
                bits = 4
                while (1 << bits) < i:
                    bits += 1
                max_len = (1 << (16 - bits)) - 1 + 3
                max_disp = 1 << bits
                best_len, best_off = 0, 0
                for off in range(1, min(i, max_disp) + 1):
                    n = 0
                    while n < max_len and i + n < len(chunk) and chunk[i + n - off] == chunk[i + n]:
                        n += 1
                    if n > best_len:
                        best_len, best_off = n, off
                if best_len >= 3:
                    group += struct.pack("<H", ((best_off - 1) << (16 - bits)) | (best_len - 3))
                    flags |= 1 << bit
                    i += best_len
                else:
                    group.append(chunk[i])
                    i += 1
            body.append(flags)
            body += group
        # 0xB000: bit 15 = compressed, bits 12-14 = signature 0b011.
        out += struct.pack("<H", 0xB000 | (len(body) - 1)) + body
    return bytes(out)


def form_b(text: str) -> bytes:
    """Header + LZNT1 as in the UEFI variable (without the 4 attribute bytes)."""
    payload = lznt1_compress(text.encode("ascii"))
    return struct.pack("<5I", 0x27000104, 0x27, 1, 5, len(payload)) + payload


def efivar(text: str) -> bytes:
    return struct.pack("<I", 7) + form_b(text)


def form_a(text: str) -> bytes:
    raw = b"\xef\xbb\xbf" + text.encode("ascii")
    return struct.pack("<7I", 0x12, 0, 0x12, 0x32, 1, 1, len(raw)) + raw


class BlobBuilder:
    """Builds the text of a synthetic blob line by line."""

    def __init__(self):
        self.channels = []
        self.algorithms = []

    def input_channel(self, guid, ch, max_, min_, trip=None, kind=1):
        trip = max_ if trip is None else trip
        self.channels.append(f"{guid},{ch},{trip},5,1000,1,1,{kind},{max_},{min_}")
        return self

    def output_channel(self, guid, ch, default, max_, min_):
        self.channels.append(f"{guid},{ch},{default},{max_},{min_}")
        return self

    def line(self, guid, id_, inputs, outputs, params, stage=0, divider=0, instance=1):
        refs = ",".join(f"{g},{c}" for g, c in list(inputs) + list(outputs))
        fields = [str(instance), str(stage), str(divider), guid, str(id_), str(len(inputs)), str(len(outputs))]
        if refs:
            fields.append(refs)
        fields += [str(p) for p in params]
        self.algorithms.append(",".join(fields))
        return self

    def text(self, crlf=True):
        nl = "\r\n" if crlf else "\n"
        parts = self.channels + ["$"] + self.algorithms + ["$", "#VNTELEMETRY", "1,10,30000,0", "#IOTELEMETRY"]
        return nl.join(parts) + nl


def vn(n):
    return (VN, n)


G = registry.GUIDS


def build(builder, variant="classic"):
    """Parse a BlobBuilder's text and return (graph, engine)."""
    from surface_power.engine.engine import Engine
    from surface_power.engine.graph import build_graph
    from surface_power.loader import parser

    graph = build_graph(parser.parse_text(builder.text()), variant=variant)
    return graph, Engine(graph)


def ref(guid, ch):
    from surface_power.loader.model import ChannelRef

    return ChannelRef(guid, ch)
