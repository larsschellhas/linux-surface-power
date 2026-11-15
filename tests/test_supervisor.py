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
        self.fs.write("sys/class/dmi/id/product_sku", "Surface_Laptop_4_1950:1951")   # Intel: no profile, direct
        self.assertIsNone(supervisor.find_profile(supervisor.detect(self.fs.root)))

    def test_shipped_profiles_load(self):
        names = {p.file for p in supervisor.load_profiles()}
        self.assertLessEqual({"surface-book-3.toml", "surface-pro-8.toml", "surface-go-3.toml", "surface-laptop-go.toml",
                              "surface-laptop-studio.toml", "surface-laptop-3-amd.toml", "surface-laptop-4-amd.toml"}, names)
        self.assertTrue(all(p.variant == "classic" for p in supervisor.load_profiles()))


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
