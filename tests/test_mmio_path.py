"""Daemon side of the MMIO write path (spec kernel-mmio-daemon.md, plan B12): the fake sysfs, path choice, writing, snapshot and
restore, foreign changes, resume. Test IDs MD-n and MB-n are those of the spec's section 12."""
import io
import json
import os
import tempfile
import unittest

from fakesys import MCHBAR, MMIO_PARAM, MMIO_ZONE, PROC_THERMAL, FakeSys
from test_daemon import blob_text

from surface_power import daemon, monitor
from surface_power.adapters import rapl_copies

MSR_ZONE = "sys/class/powercap/intel-rapl:0"
UNIT = 0.125


def raw(pl1_w, pl2_w, en1=False, en2=False, clamp1=False, clamp2=False):
    """A PL1/PL2 register in 1/8 W with enable bits 15/47 and clamp bits 16/48."""
    def half(w, en, clamp):
        return int(w / UNIT) | (1 << 15 if en else 0) | (1 << 16 if clamp else 0)
    return half(pl1_w, en1, clamp1) | (half(pl2_w, en2, clamp2) << 32)


class FakeSysMmioTest(unittest.TestCase):
    """Step 2: the fake tree has what the daemon will look at; nothing in the daemon changes."""

    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def test_module_zone(self):
        z = self.fs.add_mmio_zone()
        self.assertEqual(z, MMIO_ZONE)
        self.assertEqual(self.fs.read(f"{z}/name"), "package-0")
        self.assertEqual(self.fs.read(f"{z}/enabled"), "0")
        self.assertEqual(self.fs.read(f"{z}/constraint_0_name"), "long_term")
        self.assertEqual(self.fs.read(f"{z}/constraint_1_name"), "short_term")
        self.assertEqual(self.fs.read(f"{z}/constraint_0_power_limit_uw"), "25000000")
        self.assertEqual(self.fs.read(f"{z}/constraint_1_power_limit_uw"), "61000000")
        self.assertEqual(self.fs.read(f"{z}/constraint_0_time_window_us"), "27983872")
        self.assertEqual(self.fs.read(MMIO_PARAM), "Y")
        self.assertFalse(os.path.exists(self.fs.path(PROC_THERMAL)))
        self.assertTrue(rapl_copies.has_rapl_mmio_zone(self.fs.root))

    def test_kernel_zone(self):
        z = self.fs.add_mmio_zone("kernel")
        self.assertTrue(rapl_copies.has_proc_thermal(self.fs.root))
        self.assertFalse(os.path.exists(self.fs.path(MMIO_PARAM)))
        self.assertEqual(self.fs.read(f"{z}/constraint_2_name"), "peak_power")
        self.assertEqual(self.fs.read(f"{z}:0/name"), "dram")

    def test_register_through_the_real_read_path(self):
        self.fs.add_msr()
        self.fs.add_mmio_register(raw(25, 61, clamp1=True))
        copy, why = rapl_copies.read_mmio(self.fs.root, UNIT)
        self.assertIsNone(why)
        self.assertEqual(copy.describe(), "PL1 25 W (off, clamp), PL2 61 W (off)")
        self.assertEqual(self.fs.mmio_register(), raw(25, 61, clamp1=True))
        got = rapl_copies.read_all(self.fs.root)
        self.assertEqual(got["mmio"].raw, raw(25, 61, clamp1=True))
        self.assertEqual(MCHBAR, 0xFED10000)


class MmioDaemonBase(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.run = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.run.name, "blob.txt")
        self.write_blob()
        self.fs.write("sys/class/hwmon/hwmon2/temp9_input", 30000)
        self.out = io.StringIO()

    def write_blob(self, **kw):
        with open(self.blob, "w") as f:
            f.write(blob_text(**kw))

    def tearDown(self):
        self.fs.cleanup()
        self.run.cleanup()
        self.state.cleanup()

    def start(self, *extra):
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name,
                "--no-ec", *extra]
        return daemon.Daemon(daemon.parse_args(args), monitor.Journal(stream=self.out))

    def status(self):
        with open(os.path.join(self.run.name, "status.json")) as f:
            return json.load(f)

    def zone_files(self):
        """Every file of the MMIO zone with its content, to show that nothing was written."""
        out = {}
        base = self.fs.path(MMIO_ZONE)
        for name in sorted(os.listdir(base)):
            out[name] = self.fs.read(f"{MMIO_ZONE}/{name}")
        return out


class NoChangeTest(MmioDaemonBase):
    """Step 2: a read-only module zone next to the MSR zone changes nothing: the daemon writes the MSR zone as before."""

    def test_read_only_zone_is_left_alone(self):
        self.fs.add_mmio_zone(allow_write="N")
        before = self.zone_files()
        d = self.start("--control")
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        self.fs.write("sys/class/power_supply/ADP1/online", 0)
        d.cycle(0)
        self.assertEqual(self.fs.read(f"{MSR_ZONE}/constraint_0_power_limit_uw"), "13000000")
        self.assertEqual(self.zone_files(), before)
        d.stop()
        self.assertEqual(self.zone_files(), before)


if __name__ == "__main__":
    unittest.main()
