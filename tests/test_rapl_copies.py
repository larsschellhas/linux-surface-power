"""PL1/PL2 copies: MMIO (what Windows writes) next to MSR 0x610 (what the daemon writes), read only (spec outputs 3.3, DV-8)."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from fakesys import FakeSys
from test_daemon import blob_text

from surface_power import daemon, monitor
from surface_power.adapters import rapl_copies
from surface_power.tools import report

UNIT = 0.125                      # MSR 0x606 bits 3:0 = 3
ON = 1 << 15


def raw(pl1_w, pl2_w, en1=True, en2=True, clamp=False):
    """A PL1/PL2 register: limits in 1/8 W, enable bits 15 and 47, clamp bits 16 and 48."""
    def half(w, en):
        return int(w / UNIT) | (ON if en else 0) | ((1 << 16) if clamp else 0)
    return half(pl1_w, en1) | (half(pl2_w, en2) << 32)


class RaplCopiesBase(unittest.TestCase):
    MCHBAR = 0xFED10000

    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def msr(self, value_610):
        self.fs.add_msr({0x610: value_610})

    def host_bridge(self, mchbar=MCHBAR | 1, size=None):
        """The host bridge's PCI config; `size` cuts it (64 bytes is what a process without CAP_SYS_ADMIN sees)."""
        self.fs.add_host_bridge(mchbar, size)

    def mmio(self, value):
        def read(phys, root):
            if phys != self.MCHBAR + 0x59A0:
                raise OSError(14, "Bad address")
            return value
        return mock.patch.object(rapl_copies, "_mmio64", read)


class DecodeTest(RaplCopiesBase):
    def test_decode(self):
        c = rapl_copies.Copy(raw(25, 61, en1=False, en2=False), UNIT)
        self.assertEqual(c.pl1, (25.0, False, False))
        self.assertEqual(c.pl2, (61.0, False, False))
        self.assertIn("PL1 25 W (off), PL2 61 W (off)", c.describe())
        c = rapl_copies.Copy(raw(28, 35, clamp=True) | (1 << 63), UNIT)
        self.assertEqual(c.describe(), "PL1 28 W (on, clamp), PL2 35 W (on, clamp), locked")

    def test_mchbar_needs_the_enable_bit(self):
        self.host_bridge()
        self.assertEqual(rapl_copies.mchbar(self.fs.root), (self.MCHBAR, None))
        self.host_bridge(self.MCHBAR)
        self.assertEqual(rapl_copies.mchbar(self.fs.root), (None, "MCHBAR not enabled"))                     # MB-2
        os.remove(self.fs.path("sys/bus/pci/devices/0000:00:00.0/config"))
        addr, why = rapl_copies.mchbar(self.fs.root)
        self.assertIsNone(addr)
        self.assertTrue(why.startswith("PCI config of 00:00.0 not readable"), why)

    def test_mchbar_beyond_64_bytes_needs_cap_sys_admin(self):
        """MB-1: without CAP_SYS_ADMIN the kernel shows 64 bytes of the config and returns 0 bytes at 0x48, without an error."""
        self.host_bridge(size=64)
        self.assertEqual(rapl_copies.mchbar(self.fs.root), (None, "PCI config beyond 64 bytes needs CAP_SYS_ADMIN"))

    def test_mchbar_address_mask_is_bits_41_to_15(self):
        """MB-4: the same mask as the module (GENMASK_ULL(41, 15)); bit 40 stays, bit 45 and the low bits go."""
        self.host_bridge((1 << 45) | (1 << 40) | self.MCHBAR | 0x7FFE | 1)
        self.assertEqual(rapl_copies.mchbar(self.fs.root), ((1 << 40) | self.MCHBAR, None))
        self.assertEqual(rapl_copies.MCHBAR_ADDR_MASK, 0x3FF_FFFF_8000)

    def test_read_mmio_gives_the_reason(self):
        self.host_bridge(size=64)
        self.assertEqual(rapl_copies.read_mmio(self.fs.root, UNIT), (None, "PCI config beyond 64 bytes needs CAP_SYS_ADMIN"))
        self.host_bridge()                                                       # MB-3: no dev/mem in the fake tree
        copy, why = rapl_copies.read_mmio(self.fs.root, UNIT)
        self.assertIsNone(copy)
        self.assertTrue(why.startswith("/dev/mem not readable"), why)
        with self.mmio(raw(28, 35)):
            copy, why = rapl_copies.read_mmio(self.fs.root, UNIT)
        self.assertEqual((copy.pl1[0], why), (28.0, None))

    def test_presence_of_the_processor_thermal_device_and_zone(self):
        self.assertFalse(rapl_copies.has_proc_thermal(self.fs.root))
        self.assertFalse(rapl_copies.has_rapl_mmio_zone(self.fs.root))
        os.makedirs(self.fs.path("sys/bus/pci/devices/0000:00:04.0"))
        os.makedirs(self.fs.path("sys/class/powercap/intel-rapl-mmio:0"))
        self.assertTrue(rapl_copies.has_proc_thermal(self.fs.root))
        self.assertTrue(rapl_copies.has_rapl_mmio_zone(self.fs.root))

    def test_read_all(self):
        self.msr(raw(30, 61))
        self.host_bridge()
        with self.mmio(raw(28, 35)):
            got = rapl_copies.read_all(self.fs.root)
        self.assertEqual(got["mmio"].pl1[0], 28.0)
        self.assertIsNone(got["mmio_unreadable"])
        self.assertEqual(got["msr"].pl2[0], 61.0)
        self.host_bridge(self.MCHBAR)
        got = rapl_copies.read_all(self.fs.root)
        self.assertEqual((got["mmio"], got["mmio_unreadable"]), (None, "MCHBAR not enabled"))
        self.assertIsNone(rapl_copies.read_all(self.fs.path("nowhere")))          # no msr device: nothing readable

    def test_caps_only_for_enabled_halves_below_our_value(self):
        c = {"mmio": rapl_copies.Copy(raw(28, 35, en2=False), UNIT), "msr": None}
        got = rapl_copies.mmio_caps(c, {"pl1": 35_000_000, "pl2": 60_000_000})
        self.assertEqual(got, [("pl1", 28.0, 35.0)])                                # PL2 is off: it does not limit
        self.assertEqual(rapl_copies.mmio_caps(c, {"pl1": 28_100_000}), [])         # within the rounding tolerance
        self.assertEqual(rapl_copies.mmio_caps({"mmio": None, "msr": None}, {"pl1": 1}), [])
        self.assertEqual(rapl_copies.mmio_caps(None, {"pl1": 1}), [])


class ReportTest(RaplCopiesBase):
    def run_report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            report.main(["--root", self.fs.root, "--blob", self.blob])
        return out.getvalue()

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.tmp.name, "blob.txt")
        with open(self.blob, "w") as f:
            f.write(blob_text())

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_report_shows_the_copies(self):
        self.msr(raw(30, 61))
        self.host_bridge()
        os.makedirs(self.fs.path("sys/bus/pci/devices/0000:00:04.0"))
        with self.mmio(raw(25, 61, en1=False, en2=False)):
            text = self.run_report()
        self.assertIn("processor thermal device 00:04.0: present; powercap intel-rapl-mmio: absent", text)
        self.assertIn("MMIO (MCHBAR + 0x59A0): PL1 25 W (off), PL2 61 W (off)", text)
        self.assertIn("MSR 0x610:              PL1 30 W (on), PL2 61 W (on)", text)

    def test_report_gives_the_reason_when_the_mmio_copy_is_not_readable(self):
        self.msr(raw(30, 61))
        self.host_bridge(size=64)
        text = self.run_report()
        self.assertIn("MMIO (MCHBAR + 0x59A0): not readable (PCI config beyond 64 bytes needs CAP_SYS_ADMIN)", text)

    def test_report_without_root_says_so(self):
        text = self.run_report()
        self.assertIn("processor thermal device 00:04.0: absent", text)
        self.assertIn("copies not readable (needs root and the msr module)", text)


class DaemonTest(RaplCopiesBase):
    def setUp(self):
        super().setUp()
        self.run = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.run.name, "blob.txt")
        with open(self.blob, "w") as f:
            f.write(blob_text())
        self.out = io.StringIO()

    def tearDown(self):
        self.run.cleanup()
        self.state.cleanup()
        super().tearDown()

    def start(self):
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name,
                "--no-ec", "--dry-run"]
        return daemon.Daemon(daemon.parse_args(args), monitor.Journal(stream=self.out))

    def status(self):
        import json
        with open(os.path.join(self.run.name, "status.json")) as f:
            return json.load(f)

    def test_warns_once_when_the_mmio_copy_is_enabled_and_below_the_computed_value(self):
        self.msr(raw(30, 61))
        self.host_bridge()
        d = self.start()
        with self.mmio(raw(20, 61)):                           # PL1 20 W enabled; the blob computes 30 W
            d.cycle(0)
            d.cycle(61000)                                      # a second read, same state: no second warning
        text = self.out.getvalue()
        self.assertEqual(text.count("the MMIO copy of PL1 is 20 W"), 1)
        self.assertNotIn("MMIO copy of PL2", text)
        self.assertEqual(self.status()["rapl_copies"]["mmio_below_computed"], {"PL1": 20.0})
        with self.mmio(raw(20, 61, en1=False)):                 # switched off: it no longer limits
            d.cycle(122000)
        self.assertIn("MMIO copy of PL1 no longer limits", self.out.getvalue())
        d.stop()

    def test_silent_when_the_mmio_copy_is_off(self):
        """MB-9 (first half): an off copy does not limit, so nothing is said."""
        self.msr(raw(30, 61))
        self.host_bridge()
        d = self.start()
        with self.mmio(raw(25, 61, en1=False, en2=False)):      # SB3 15": both enable bits 0
            d.cycle(0)
        self.assertNotIn("MMIO copy", self.out.getvalue())
        self.assertNotIn("mmio_below_computed", self.status()["rapl_copies"])
        self.assertIsNone(self.status()["rapl_copies"]["mmio_unreadable"])
        d.stop()

    def unreadable(self, reason_start):
        """MB-9 (second half): one line per run with the reason, the status keeps the reason, no warning about a lower copy."""
        d = self.start()
        d.cycle(0)
        d.cycle(61000)                                          # a second read, same reason: no second line
        text = self.out.getvalue()
        self.assertEqual(text.count("the MMIO copy of PL1/PL2 is not readable"), 1, text)
        self.assertIn(f"not readable ({reason_start}", text)
        self.assertNotIn("below the computed", text)
        self.assertTrue(self.status()["rapl_copies"]["mmio_unreadable"].startswith(reason_start))
        self.assertIsNone(self.status()["rapl_copies"]["mmio"])
        d.stop()

    def test_one_line_when_the_config_shows_only_64_bytes(self):
        """MB-1: the view under the packaged units (no CAP_SYS_ADMIN)."""
        self.msr(raw(30, 61))
        self.host_bridge(size=64)
        self.unreadable("PCI config beyond 64 bytes needs CAP_SYS_ADMIN")

    def test_one_line_when_mchbar_is_not_enabled(self):
        """MB-2"""
        self.msr(raw(30, 61))
        self.host_bridge(self.MCHBAR)
        self.unreadable("MCHBAR not enabled")

    def test_one_line_when_dev_mem_is_not_readable(self):
        """MB-3: no dev/mem in the fake tree."""
        self.msr(raw(30, 61))
        self.host_bridge()
        self.unreadable("/dev/mem not readable")

if __name__ == "__main__":
    unittest.main()
