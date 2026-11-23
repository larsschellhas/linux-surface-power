"""Tests for the supervisor parts: detection, snapshot/restore, emergency rules, foreign changes (spec supervisor; AC-S*)."""
import json
import os
import tempfile
import unittest

from fakesys import FakeSys
from helpers import TEMP, ref

from surface_power import supervisor
from surface_power.tools import reset as reset_tool

PL1 = "sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"
TW = "sys/class/powercap/intel-rapl:0/constraint_0_time_window_us"
PSYS1 = "sys/class/powercap/intel-rapl:1/constraint_0_power_limit_uw"


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def test_sb3(self):
        d = supervisor.detect(self.fs.root)
        self.assertTrue(d.microsoft)
        self.assertTrue(d.smf)
        self.assertEqual(d.product, "Surface Book 3")
        p = supervisor.find_profile(d)
        self.assertIsNotNone(p)
        self.assertEqual(p.variant, "classic")

    def test_not_microsoft(self):
        self.fs.write("sys/class/dmi/id/sys_vendor", "LENOVO")
        d = supervisor.detect(self.fs.root)
        self.assertFalse(d.supported)

    def test_no_smf(self):
        os.rmdir(self.fs.path("sys/bus/acpi/devices/MSHW0187:00"))
        self.assertFalse(supervisor.detect(self.fs.root).supported)

    def test_unknown_smf_device_has_no_profile(self):
        self.fs.write("sys/class/dmi/id/product_name", "Surface Laptop 9")
        d = supervisor.detect(self.fs.root)
        self.assertTrue(d.supported)
        self.assertIsNone(supervisor.find_profile(d))

    def test_profile_by_sku(self):
        self.fs.write("sys/class/dmi/id/product_name", "Surface Laptop 4")
        self.fs.write("sys/class/dmi/id/product_sku", "Surface_Laptop_4_1958:1959")
        p = supervisor.find_profile(supervisor.detect(self.fs.root))
        self.assertEqual((p.file, p.variant), ("surface-laptop-4-amd.toml", "classic"))
        self.fs.write("sys/class/dmi/id/product_sku", "Surface_Laptop_4_1950:1951")   # Intel: its own profile, direct
        p = supervisor.find_profile(supervisor.detect(self.fs.root))
        self.assertEqual((p.file, p.variant, p.pl3), ("surface-laptop-4-intel.toml", "direct", True))
        self.fs.write("sys/class/dmi/id/product_sku", "Surface_Laptop_4_1999:1999")   # unknown SKU: no profile
        self.assertIsNone(supervisor.find_profile(supervisor.detect(self.fs.root)))

    def test_shipped_profiles_load(self):
        names = {p.file for p in supervisor.load_profiles()}
        self.assertLessEqual({"surface-book-3.toml", "surface-pro-8.toml", "surface-go-3.toml", "surface-laptop-go.toml",
                              "surface-laptop-studio.toml", "surface-laptop-3-amd.toml", "surface-laptop-4-amd.toml"}, names)
        classic = {"surface-book-3.toml", "surface-pro-8.toml", "surface-go-3.toml", "surface-laptop-go.toml",
                   "surface-laptop-studio.toml", "surface-laptop-3-amd.toml", "surface-laptop-4-amd.toml"}
        self.assertTrue(all((p.variant == "classic") == (p.file in classic) for p in supervisor.load_profiles()))
        self.assertFalse(any(p.tested for p in supervisor.load_profiles()))
        # no SKU is matched by two profiles
        skus = [s for p in supervisor.load_profiles() for s in (p.sku or ())]
        self.assertEqual(len(skus), len(set(skus)))
        pro10 = next(p for p in supervisor.load_profiles() if p.file == "surface-pro-10.toml")
        self.assertEqual((pro10.product, pro10.variant, pro10.tested), ("Surface Pro 10 for Business", "direct", False))
        self.assertEqual((pro10.pl4_requires_ec, pro10.pl4_ec_wait_s), (True, 10.0))
        pro9 = next(p for p in supervisor.load_profiles() if p.file == "surface-pro-9.toml")
        self.assertEqual((pro9.variant, pro9.tested), ("direct", False))
        # PL3 only where Windows' write of MSR 0x615 is verified in the device's driver (docs/research/10-outputs.md 4.1)
        # (Go 3/Go 4: platform type from the ACPI _DSM of the public DSDT dumps)
        self.assertEqual({p.file for p in supervisor.load_profiles() if p.pl3 is True},
                         {"surface-book-3.toml", "surface-pro-8.toml", "surface-pro-9.toml", "surface-pro-10.toml",
                          "surface-laptop-go.toml", "surface-laptop-studio.toml", "surface-laptop-studio-2.toml",
                          "surface-pro-7-plus.toml", "surface-pro-10-5g.toml", "surface-pro-11-intel.toml",
                          "surface-laptop-4-intel.toml", "surface-laptop-5.toml", "surface-laptop-6.toml",
                          "surface-laptop-7-intel.toml", "surface-laptop-7-5g.toml", "surface-laptop-go-2.toml",
                          "surface-laptop-go-3.toml", "surface-go-3.toml", "surface-go-4.toml"})
        self.assertTrue(any("integrated GPU" in n for n in pro10.notes))
        self.assertFalse(any(p.pl4_requires_ec or p.notes for p in supervisor.load_profiles() if p.file != "surface-pro-10.toml"))


class DgpuProfileOptionTest(unittest.TestCase):
    """dGPU stand-in options (spec outputs 6.2): the Laptop Studio profiles ship with both off; the SB3 has it on."""

    def test_shipped_profiles(self):
        by = {p.file: p for p in supervisor.load_profiles()}
        self.assertTrue(by["surface-book-3.toml"].dgpu_clock_cap)
        for name in ("surface-laptop-studio.toml", "surface-laptop-studio-2.toml"):
            self.assertFalse(by[name].dgpu_clock_cap, name)
            self.assertFalse(by[name].dgpu_clock_cap_dry_run, name)

    def test_option_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "x.toml"), "w") as f:
                f.write('[match]\nproduct_name = "X"\n[outputs]\ndgpu_clock_cap_dry_run = true\n')
            p = supervisor.load_profiles(td)[0]
            self.assertEqual((p.dgpu_clock_cap, p.dgpu_clock_cap_dry_run), (False, True))


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.run = tempfile.TemporaryDirectory()
        self.paths = [self.fs.path(p) for p in (TW, PL1, PSYS1)]

    def tearDown(self):
        self.fs.cleanup()
        self.run.cleanup()

    def test_save_restore_remove(self):
        snap, inherited = supervisor.Snapshot.take(self.run.name, self.paths)
        self.assertFalse(inherited)
        self.fs.write(PL1, 12000000)
        self.fs.write(TW, 1000)
        self.assertEqual(snap.restore(), [])
        self.assertEqual(self.fs.read(PL1), "30000000")
        self.assertEqual(self.fs.read(TW), "27983872")
        snap.remove()
        self.assertFalse(os.path.exists(os.path.join(self.run.name, "snapshot.json")))

    def test_predecessor_snapshot_is_kept(self):
        supervisor.Snapshot.take(self.run.name, self.paths)
        self.fs.write(PL1, 12000000)                       # values from a crashed predecessor
        snap, inherited = supervisor.Snapshot.take(self.run.name, self.paths)
        self.assertTrue(inherited)
        self.assertEqual(snap.values[self.fs.path(PL1)], "30000000")

    def test_time_window_restored_first(self):
        snap, _ = supervisor.Snapshot.take(self.run.name, [self.fs.path(PL1), self.fs.path(TW)])
        self.assertEqual(snap.order()[0], self.fs.path(TW))

    def test_reset_tool(self):  # FR-S2: ExecStopPost after kill -9
        supervisor.Snapshot.take(self.run.name, self.paths)
        self.fs.write(PL1, 12000000)
        self.assertEqual(reset_tool.main(["--run-dir", self.run.name]), 0)
        self.assertEqual(self.fs.read(PL1), "30000000")
        self.assertFalse(os.path.exists(os.path.join(self.run.name, "snapshot.json")))
        self.assertEqual(reset_tool.main(["--run-dir", self.run.name]), 0)   # nothing to do

    def test_snapshot_file_format(self):
        supervisor.Snapshot.take(self.run.name, self.paths)
        with open(os.path.join(self.run.name, "snapshot.json")) as f:
            data = json.load(f)
        self.assertEqual(data["version"], 1)
        self.assertIn(self.fs.path(PL1), data["values"])


class EmergencyTest(unittest.TestCase):
    def test_skin_hot(self):
        g = supervisor.Guard(skin=[ref(TEMP, 509)])
        for t in range(0, 10000, 1000):                       # >= 60 °C, but not yet for 10 s
            self.assertIsNone(g.check(t, {ref(TEMP, 509): 3331}, core_invalid=False))
        self.assertIn("skin", g.check(10000, {ref(TEMP, 509): 3331}, core_invalid=False))
        self.assertTrue(g.emergency)
        for t in range(11000, 71000, 1000):                   # below 55 °C, but not yet for 60 s
            g.check(t, {ref(TEMP, 509): 3280}, core_invalid=False)
            self.assertTrue(g.emergency)
        g.check(71000, {ref(TEMP, 509): 3280}, core_invalid=False)
        self.assertFalse(g.emergency)

    def test_cpu_hot(self):
        g = supervisor.Guard(skin=[], cpu=ref(TEMP, 102))
        for t in range(0, 11000, 1000):
            reason = g.check(t, {ref(TEMP, 102): 3781}, core_invalid=False)
        self.assertIn("CPU", reason)

    def test_core_inputs_lost(self):
        g = supervisor.Guard(skin=[])
        for t in range(0, 61000, 1000):
            reason = g.check(t, {}, core_invalid=True)
        self.assertIn("inputs", reason)
        for t in range(61000, 72000, 1000):
            g.check(t, {}, core_invalid=False)
        self.assertFalse(g.emergency)


class ForeignTest(unittest.TestCase):
    def test_yield_after_three_in_60_s(self):
        f = supervisor.ForeignWatch()
        self.assertFalse(f.record(0))
        self.assertFalse(f.record(30000))
        self.assertTrue(f.record(59000))

    def test_old_changes_expire(self):
        f = supervisor.ForeignWatch()
        f.record(0)
        f.record(10000)
        self.assertFalse(f.record(80000))


if __name__ == "__main__":
    unittest.main()
