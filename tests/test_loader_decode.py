"""Tests for LZNT1 and the delivery forms (spec blob-format 2, 3; AC-B4)."""
import struct
import unittest

from helpers import efivar, form_a, form_b, lznt1_compress

from surface_power.loader import container, lznt1
from surface_power.loader.errors import BlobError

SAMPLE = "A,1,2\r\n" * 50 + "B,123456789,0.4\r\n$\r\n"


class Lznt1Test(unittest.TestCase):
    def test_roundtrip_compressed(self):
        data = (SAMPLE * 20).encode()
        self.assertEqual(lznt1.decompress(lznt1_compress(data)), data)

    def test_uncompressed_chunk(self):
        raw = b"hello, world"
        stream = struct.pack("<H", 0x3000 | (len(raw) - 1)) + raw
        self.assertEqual(lznt1.decompress(stream), raw)

    def test_stops_at_zero_header(self):
        raw = b"abc"
        stream = struct.pack("<H", 0x3000 | 2) + raw + b"\x00\x00garbage"
        self.assertEqual(lznt1.decompress(stream), raw)

    def test_truncated_chunk_fails(self):
        stream = lznt1_compress(SAMPLE.encode())
        with self.assertRaises(BlobError):
            lznt1.decompress(stream[: len(stream) // 2])

    def test_bad_signature_fails(self):
        stream = bytearray(lznt1_compress(SAMPLE.encode()))
        stream[1] = (stream[1] & 0x8F) | 0x40  # signature bits 0b100
        with self.assertRaises(BlobError):
            lznt1.decompress(bytes(stream))

    def test_displacement_before_chunk_start_fails(self):
        # flag byte with bit 0 set: the first item is a copy token at position 0, which cannot refer back.
        body = b"\x01" + struct.pack("<H", 0x0000)
        stream = struct.pack("<H", 0xB000 | (len(body) - 1)) + body
        with self.assertRaises(BlobError):
            lznt1.decompress(stream)

    def test_output_limit(self):
        data = b"x" * 5000
        with self.assertRaises(BlobError):
            lznt1.decompress(lznt1_compress(data), limit=4096)


class ContainerTest(unittest.TestCase):
    def test_form_b(self):
        res = container.decode(form_b(SAMPLE))
        self.assertEqual(res.form, "B")
        self.assertEqual(res.text, SAMPLE)

    def test_efivar_strips_attributes(self):
        res = container.decode(efivar(SAMPLE), efivar=True)
        self.assertEqual(res.form, "B")
        self.assertEqual(res.text, SAMPLE)

    def test_form_b_wrong_length_fails(self):
        data = bytearray(form_b(SAMPLE))
        struct.pack_into("<I", data, 16, len(data))  # length field too large
        with self.assertRaises(BlobError) as cm:
            container.decode(bytes(data))
        self.assertIn("length", str(cm.exception))

    def test_form_a(self):
        res = container.decode(form_a(SAMPLE))
        self.assertEqual(res.form, "A")
        self.assertEqual(res.text, SAMPLE)

    def test_form_c(self):
        res = container.decode(lznt1_compress(SAMPLE.encode()))
        self.assertEqual(res.form, "C")
        self.assertEqual(res.text, SAMPLE)

    def test_plain_text(self):
        res = container.decode(SAMPLE.encode())
        self.assertEqual(res.form, "text")
        self.assertEqual(res.text, SAMPLE)

    def test_capsule_with_embedded_form_b(self):
        capsule = b"\x00\xff" * 700 + form_b(SAMPLE) + b"\x00" * 64
        res = container.decode(capsule)
        self.assertEqual(res.form, "capsule/B")
        self.assertEqual(res.text, SAMPLE)

    def test_capsule_with_embedded_form_a(self):
        capsule = b"\x00\xff" * 300 + form_a(SAMPLE) + b"\x00" * 16
        res = container.decode(capsule)
        self.assertEqual(res.form, "capsule/A")
        self.assertEqual(res.text, SAMPLE)

    def test_unknown_format_fails(self):
        with self.assertRaises(BlobError):
            container.decode(b"\x00\x01\x02\x03" * 100)

    def test_empty_fails(self):
        with self.assertRaises(BlobError):
            container.decode(b"")


if __name__ == "__main__":
    unittest.main()
