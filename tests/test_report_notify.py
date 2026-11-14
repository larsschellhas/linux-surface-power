"""Tests for the report tool (FR-M7, spec tools 3) and the notifier logic (FR-M5, spec monitoring 4)."""
import contextlib
import io
import os
import tempfile
import unittest

from fakesys import FakeSys
from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G, efivar

from surface_power.tools import notify, report


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        b = BlobBuilder()
        b.input_channel(TEMP, 509, 3681, 2731)
        b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 1, 15, 4, 0], divider=1)
        b.line(G["pid_parameter_set"], 1, [], [], [1, 1, 1, 30000, 30001, 7500, 21234, 3211, 10, 40, 100, 0.4])
        b.line(G["pass_through"], 2, [(VN, 1)], [(LIMITS, 51)], [15678], stage=1)
        self.fs.write("sys/firmware/efi/efivars/SMF_DATA_BLOB-a3d05fa9-db5e-490c-956e-a57384a4b70b", "")
        with open(self.fs.path("sys/firmware/efi/efivars/SMF_DATA_BLOB-a3d05fa9-db5e-490c-956e-a57384a4b70b"), "wb") as f:
            f.write(efivar(b.text()))

    def tearDown(self):
        self.fs.cleanup()

    def test_report_has_no_blob_content(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = report.main(["--root", self.fs.root])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Surface Book 3", text)
        self.assertIn("sha256", text)
        self.assertIn("2E8B14C2/509: provided", text)
        self.assertIn("0B7B5350/51", text)
        body = "\n".join(line for line in text.splitlines() if "sha256" not in line)
        for value in ("21234", "15678", "30001", "7500"):      # parameter values of the blob
            self.assertNotIn(value, body)


class NotifyTest(unittest.TestCase):
    def test_transitions(self):
        n = notify.Notifier()
        self.assertEqual(n.update({"state": "control"}, 0), [])
        msgs = n.update({"state": "emergency", "events": [{"text": "state control -> emergency: skin"}]}, 5000)
        self.assertEqual(len(msgs), 1)
        self.assertIn("emergency", msgs[0][0].lower())
        self.assertEqual(n.update({"state": "emergency"}, 10000), [])
        self.assertEqual(len(n.update({"state": "control"}, 15000)), 1)

    def test_weak_charger(self):
        n = notify.Notifier()
        st = {"state": "control", "slider": {"ac": True},
              "inputs": [{"channel": "28B7BD85/10", "value": 5000, "valid": True}]}
        for t in range(0, 60000, 5000):
            self.assertEqual(n.update(st, t), [])
        self.assertEqual(len(n.update(st, 61000)), 1)
        self.assertEqual(n.update(st, 120000), [])          # not again within 10 minutes


if __name__ == "__main__":
    unittest.main()
