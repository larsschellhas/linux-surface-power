"""Tests for the report tool (FR-M7, spec tools 3) and the notifier logic (FR-M5, spec monitoring 4)."""
import contextlib
import io
import os
import tempfile
import unittest

from fakesys import FakeSys
from helpers import LIMITS, POWER, SYSTEM, TEMP, VN, BlobBuilder, G, efivar

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


class ReportSourcesTest(unittest.TestCase):
    """The report says where the EC power data come from and whether the dGPU stand-in would write (tester guide)."""

    def setUp(self):
        self.fs = FakeSys()
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [(POWER, 8)], [(LIMITS, 201)], [50000], stage=1)
        self.blob = self.fs.path("blob.txt")
        with open(self.blob, "w") as f:
            f.write(b.text())

    def tearDown(self):
        self.fs.cleanup()

    def run_report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report.main(["--root", self.fs.root, "--blob", self.blob])
        return out.getvalue()

    def test_without_kernel_attributes(self):
        text = self.run_report()
        self.assertIn("EC power data: debug device only", text)
        self.assertIn("28B7BD85/8: needs the debug device", text)
        # No NVIDIA GPU in this fake system: the dGPU limit has nothing to act on (the daemon and the report agree, B10).
        self.assertIn("0B7B5350/201: not needed on Linux: no NVIDIA dGPU in this machine", text)
        self.assertIn("ready for control (as this user; run the report as root for the daemon's answer under its unit): yes", text)

    def test_hint_when_root_and_cdev_would_help(self):
        text = self.run_report()
        self.assertIn("run as root with surface_aggregator_cdev loaded: 0 more outputs and", text)
        self.assertNotIn("FAN 0x13", text)

    def test_with_new_dptf_group(self):
        for bat in ("BAT1", "BAT2"):
            self.fs.write(f"sys/class/power_supply/{bat}/dptf/available_input_power_mw", 73113)
        self.fs.write("sys/class/power_supply/ADP1/dptf/adapter_rating_mw", 120000)
        self.assertIn("EC power data: kernel attributes", self.run_report())

    def test_with_kernel_attributes_and_gpu(self):
        for bat in ("BAT1", "BAT2"):
            self.fs.write(f"sys/class/power_supply/{bat}/dptf_battery/available_input_power_mw", 73113)
        self.fs.write("sys/class/power_supply/ADP1/dptf_power/adapter_rating_mw", 120000)
        self.fs.write("sys/bus/pci/devices/0000:02:00.0/vendor", "0x10de")
        self.fs.write("sys/bus/pci/devices/0000:02:00.0/class", "0x030200")
        os.makedirs(self.fs.path("sys/module/surface_fan"))
        text = self.run_report()
        self.assertIn("EC power data: kernel attributes", text)
        self.assertIn("28B7BD85/8: provided", text)
        self.assertIn("0B7B5350/201: clock-cap stand-in (DO-1)", text)
        self.assertIn("surface_fan", text)


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

    def test_thermal_requests(self):
        n = notify.Notifier()
        st = {"state": "control", "thermal_actions": {"hot_active": True, "critical_active": False}}
        self.assertIn("too hot", n.update(st, 0)[0][0])
        st["thermal_actions"]["critical_active"] = True
        self.assertIn("critical", n.update(st, 1000)[0][0])
        self.assertEqual(n.update(st, 2000), [])


if __name__ == "__main__":
    unittest.main()


class ReportEcTest(unittest.TestCase):
    """--with-ec on a Pro 9 like device (field report): reads per field, and only the commands the blob needs."""

    def setUp(self):
        self.fs = FakeSys()
        b = BlobBuilder()
        for c in (2, 3, 6, 7, 8, 13):
            b.line(G["pass_through"], c, [(POWER, c)], [(VN, c)], [1000], stage=1)
        b.line(G["pass_through"], 20, [(VN, 2)], [(LIMITS, 51)], [30000], stage=1)
        self.blob = self.fs.path("blob.txt")
        with open(self.blob, "w") as f:
            f.write(b.text())
        self.sent = []
        self.rating = [0, 38745]

        outer = self

        class Ec:
            def available(self):
                return True

            def request(self, tid, cid, iid=1):
                import struct
                outer.sent.append((tid, cid))
                if (tid, cid) == (1, 0x53):
                    return bytes([1]) + struct.pack("<IIHHII", 142770, 82840, 100, 8000, 0, 0)
                if cid == 0x0F:
                    return outer.rating.pop(0).to_bytes(4, "little")
                if cid == 0x1A:
                    return bytes([20])
                return None                                          # everything else times out

        self.ec = Ec()

    def tearDown(self):
        self.fs.cleanup()

    def run_report(self):
        out = io.StringIO()
        old, report.SLEEP = report.SLEEP, lambda s: None
        try:
            with contextlib.redirect_stdout(out):
                report.main(["--root", self.fs.root, "--blob", self.blob, "--with-ec"], ec_transport=self.ec)
        finally:
            report.SLEEP = old
        return out.getvalue()

    def test_fields_adapter_and_unneeded_commands(self):
        text = self.run_report()
        self.assertIn("EC 0x53 request, battery 1 (SAM): answered 21 bytes; peak power 142770 mW", text)
        self.assertIn("available input power 0 mW (zero)", text)
        self.assertNotIn("no answer", text)
        self.assertIn("first request 0 mW on AC, second request after 2 s 38745 mW", text)
        self.assertIn("EC adapter rating request: answered 38745 mW", text)
        self.assertIn("EC battery state answers: 13=20", text)
        self.assertIn("not sent: the blob does not read it", text)
        self.assertIn("28B7BD85/8: provided", text)                  # a field that reads 0 still answered
        self.assertIn("28B7BD85/7: provided", text)
        self.assertEqual(sorted(set(self.sent)), [(1, 0x0F), (1, 0x1A), (1, 0x53)])   # no 0x69, no second battery
        self.assertIn("platform profile: performance (/sys/firmware/acpi/platform_profile)", text)

    def test_warns_about_limit_writers(self):
        self.fs.write("proc/321/comm", "thermald")
        self.assertIn("WARNING: thermald running", self.run_report())
