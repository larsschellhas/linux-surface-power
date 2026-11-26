"""B13: the Surface Book 3 base detached while the daemon runs (spec supervisor 4.1, raw note b13-basis-sensoren-2026-11-24.md).

The base sensors 2E8B14C2/601-699 exist only while the base is attached. A missing source device makes them absent (not lost): they do not
count for the emergency rule, the blob runs on its block defaults, and the hwmon path is looked up again (the index can change). Test IDs
B13-n are those of the raw note, section 7, with the maintainer's decisions B13-7 (a running emergency ends) and B13-8 (the dGPU stand-in
is switched on when 609 returns)."""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from fakesys import FakeSys
from helpers import LIMITS, TEMP
from test_daemon import blob_text

from surface_power import daemon, monitor
from surface_power.adapters import inputs as inp
from surface_power.loader.model import ChannelRef

PL1 = "sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"
PSYS1 = "sys/class/powercap/intel-rapl:1/constraint_0_power_limit_uw"
B609, B608 = ChannelRef(TEMP, 609), ChannelRef(TEMP, 608)
ACTION = "110DF2A4-5995-473C-92FD-3CAC40EE2E6C"


def action_line(channel):
    """A pass-through from TEMP/<channel> to the hibernate action 110DF2A4/1, default 1 (no request) while the input is invalid."""
    return f"1,0,0,026CCFCC-A52A-4DB1-8B49-D2BAAFCAD329,40,1,1,2E8B14C2-26D6-4119-A62E-9D108C4DDC2A,{channel},{ACTION},1,1"


class BaseDetachTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.run = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.run.name, "blob.txt")
        self.write_blob(absent_sensor=True)                          # controller on 609 -> dGPU limit 201 (as on the SB3)
        self.fs.write("sys/class/hwmon/hwmon2/temp9_input", 30000)
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        self.fs.write("sys/class/power_supply/ADP1/online", 0)       # balanced on battery: PL1 13 W, not the firmware's 30 W
        self.out = io.StringIO()

    def tearDown(self):
        self.fs.cleanup()
        self.run.cleanup()
        self.state.cleanup()

    def write_blob(self, **kw):
        with open(self.blob, "w") as f:
            f.write(blob_text(**kw))

    def start(self, *extra):
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name,
                "--no-ec", "--control", *extra]
        return daemon.Daemon(daemon.parse_args(args), monitor.Journal(stream=self.out))

    def status(self):
        with open(os.path.join(self.run.name, "status.json")) as f:
            return json.load(f)

    def cycles(self, d, start_ms, end_ms, step=1000):
        for t in range(start_ms, end_ms, step):
            d.cycle(t)

    def log(self):
        return self.out.getvalue()

    # --- B13-1, B13-15: detach while running ---
    def test_b13_1_detach_is_absent_not_lost(self):
        self.fs.add_base_sensors()
        d = self.start()
        self.cycles(d, 0, 10000)
        self.assertEqual(d.values[B609], inp.mc_to_dk(30000))
        self.fs.remove_base_sensors()
        self.cycles(d, 10000, 80000)
        self.assertEqual(d.state, "control")
        self.assertEqual(d.reader.absent, {B609})
        self.assertIsNone(d.values[B609])
        self.assertEqual(self.fs.read(PL1), "13000000")                # the limits stay in control
        self.assertEqual(self.log().count("absent: its source device is gone"), 1)
        self.assertEqual(self.status()["inputs_absent"], ["2E8B14C2/609"])
        ctl = next(c for c in self.status()["controllers"] if c["input"] == "2E8B14C2/609")
        self.assertEqual(ctl["state"], "OFF")
        d.stop()

    # --- B13-3, B13-4, B13-5: re-attach ---
    def reattach(self, index, **kw):
        self.fs.add_base_sensors()
        d = self.start()
        self.cycles(d, 0, 5000)
        self.fs.remove_base_sensors()
        self.cycles(d, 5000, 35000)
        self.fs.readd_base_sensors(index, celsius=31.0, **kw)
        self.cycles(d, 35000, 45000)
        return d

    def test_b13_3_back_under_the_same_index(self):
        d = self.reattach(10)
        self.assertEqual(d.values[B609], inp.mc_to_dk(31000))
        self.assertEqual(d.reader.absent, set())
        self.assertIn("back", self.log())
        self.assertNotIn("inputs_absent", self.status())
        self.assertEqual(d.state, "control")
        d.stop()

    def test_b13_4_back_under_a_new_index(self):
        d = self.reattach(12)
        self.assertEqual(d.values[B609], inp.mc_to_dk(31000))
        self.assertEqual(d.reader.absent, set())
        d.stop()

    def test_b13_5_foreign_device_under_the_old_index(self):
        """7.3.6-1-surface: after the re-attach hwmon5 was a fan [M]; a device with another name or SSAM target is not taken."""
        for kw in ({"name": "nvme"}, {"device": "sys/devices/platform/MSHW0117:00/00:00:01:11:00/01:05:02:02:01"}):
            d = self.reattach(10, **kw)
            self.assertIsNone(d.values[B609], kw)
            self.assertEqual(d.reader.absent, {B609})
            d.stop()
            self.fs.remove_base_sensors()

    def test_index_reused_by_another_device_while_the_path_still_exists(self):
        """The old index now holds a device of another SSAM target that also has temp9_input: its value is never read as 609."""
        self.fs.add_base_sensors(celsius=30.0)
        d = self.start()
        self.cycles(d, 0, 3000)
        self.fs.readd_base_sensors(10, celsius=80.0, name="surface_thermal",
                                   device="sys/devices/platform/MSHW0117:00/serial0-0/01:03:01:00:02")
        self.cycles(d, 3000, 10000)
        self.assertNotEqual(d.values[B609], inp.mc_to_dk(80000))
        d.stop()

    # --- B13-6, B13-7: a present device that cannot be read ---
    def break_base_read(self):
        p = self.fs.path("sys/class/hwmon/hwmon10/temp9_input")
        os.remove(p)
        os.makedirs(p)                                                  # read fails with EISDIR: device present, read lost

    def test_b13_6_read_error_of_a_present_device_is_lost(self):
        self.fs.add_base_sensors()
        d = self.start()
        self.cycles(d, 0, 3000)
        self.break_base_read()
        self.cycles(d, 3000, 66000)
        self.assertEqual(d.state, "emergency")
        self.assertEqual(d.reader.absent, set())
        os.rmdir(self.fs.path("sys/class/hwmon/hwmon10/temp9_input"))
        self.fs.write("sys/class/hwmon/hwmon10/temp9_input", 30000)
        self.cycles(d, 66000, 78000)
        self.assertEqual(d.state, "control")
        d.stop()

    def test_b13_7_emergency_ends_when_the_input_becomes_absent(self):
        self.fs.add_base_sensors()
        d = self.start()
        self.cycles(d, 0, 3000)
        self.break_base_read()
        self.cycles(d, 3000, 66000)
        self.assertEqual(d.state, "emergency")
        self.fs.remove_base_sensors()
        self.cycles(d, 66000, 80000)
        self.assertEqual(d.state, "control")
        self.assertEqual(d.reader.absent, {B609})
        d.stop()

    # --- B13-8: start without the base ---
    def test_b13_8_start_without_base_then_attach(self):
        from test_gpu import PCI
        self.fs.write(f"sys/bus/pci/devices/{PCI}/vendor", "0x10de")        # an SB3 15" with its dGPU
        self.fs.write(f"sys/bus/pci/devices/{PCI}/class", "0x030200")
        d = self.start()                                               # no --allow-partial-outputs: the stand-in waits, 201 has a path
        self.assertEqual(d.mode, "control")
        self.assertEqual(d.reader.absent, {B609})
        self.assertNotIn(B609, d.reader.unavailable)
        self.assertIsNone(d.dgpu)
        self.assertIsNotNone(d.dgpu_pending)
        self.assertIn("waits for input 2E8B14C2/609", self.log())
        self.cycles(d, 0, 30000)
        self.assertEqual(d.state, "control")
        self.fs.readd_base_sensors(11)
        self.cycles(d, 30000, 40000)
        self.assertEqual(d.values[B609], inp.mc_to_dk(31000))
        self.assertIsNotNone(d.dgpu)
        self.assertIn("dGPU stand-in on: input 2E8B14C2/609 is back", self.log())
        d.stop()

    def test_absent_at_start_reaching_only_a_thermal_action_allows_control(self):
        """The base part of the hibernate/shutdown criteria falls to its block default (1, no request), as on Windows without the
        base client; the SAM part stays. A missing input that is not detachable still forces dry-run (unchanged)."""
        self.write_blob(extra=[action_line(609)])
        d = self.start()
        self.assertEqual(d.mode, "control")
        d.stop()
        self.write_blob(extra=[action_line(550)])                      # SAM 550: not provided, not detachable
        d = self.start()
        self.assertEqual(d.mode, "dry-run")
        self.assertIn("2E8B14C2/550", d.mode_reason)
        d.stop()

    def test_no_dgpu_means_the_dgpu_limit_is_not_needed(self):
        """A model without an NVIDIA dGPU (SB3 13" i5): the dGPU limit counts as not needed, no waiting stand-in (report review 25.11.)."""
        d = self.start()
        self.assertIsNone(d.dgpu_pending)
        self.assertIn(daemon.DGPU, d.not_needed)
        self.assertEqual(d.mode, "control")
        d.stop()

    # --- B13-9: absent input that reaches a written limit ---
    def test_b13_9_absent_input_on_a_written_limit_counts_as_lost(self):
        self.write_blob(absent_sensor=True, absent_on_written=True)  # 608 -> psys PL1 (54)
        self.fs.add_base_sensors()
        d = self.start()
        self.assertEqual(d.mode, "control")
        self.cycles(d, 0, 3000)
        self.fs.remove_base_sensors()
        self.cycles(d, 3000, 66000)
        self.assertEqual(d.state, "emergency")
        self.assertIn("2E8B14C2/608", self.log())
        d.stop()

    def test_b13_9_absent_at_start_on_a_written_limit_forces_dry_run(self):
        self.write_blob(absent_sensor=True, absent_on_written=True)
        d = self.start()
        self.assertEqual(d.mode, "dry-run")
        self.assertIn("2E8B14C2/608", d.mode_reason)
        d.stop()

    # --- B13-13: cost of the look-up ---
    def test_b13_13_lookups_are_rate_limited(self):
        self.fs.add_base_sensors()
        d = self.start()
        hw = next(s for s in d.reader.sources if isinstance(s, inp.HwmonTemperatures))
        n0 = hw.lookups
        self.cycles(d, 0, 600000, 1000)                                # 10 min with the base attached
        self.assertEqual(hw.lookups, n0)
        self.fs.remove_base_sensors()
        n1 = hw.lookups
        self.cycles(d, 600000, 1200000, 1000)                          # 10 min without
        self.assertLessEqual(hw.lookups - n1, 600 // 5 + 1)
        d.stop()

    # --- B13-11: the stand-in when the dGPU falls off the bus ---
    def test_b13_11_stand_in_survives_nvml_errors_while_609_is_absent(self):
        from helpers import SYSTEM, BlobBuilder, G
        from test_gpu import PCI, FakeNvml
        b = BlobBuilder()
        b.line(G["lookup_table"], 20, [(SYSTEM, 1)], [(LIMITS, 201)], [6, 50000, 50000, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6,
                                                                     50000, 50000, 50000, 50000, 50000, 50000])
        with open(self.blob, "w") as f:
            f.write(blob_text(absent_sensor=True, extra=b.algorithms))
        self.fs.write(f"sys/bus/pci/devices/{PCI}/vendor", "0x10de")
        self.fs.write(f"sys/bus/pci/devices/{PCI}/class", "0x030200")
        self.fs.write(f"sys/bus/pci/devices/{PCI}/power/runtime_status", "active")
        self.fs.add_base_sensors()
        nvml = FakeNvml()
        from fakehelper import HelperThread
        sock = self.fs.path("run/surface-gpu-clock/sock")
        os.makedirs(os.path.dirname(sock), exist_ok=True)
        self.addCleanup(HelperThread(sock, self.fs.root, nvml).stop)
        d = self.start("--dgpu-clock-cap")
        d.dgpu.factory = lambda pci: nvml
        self.cycles(d, 0, 4000, 2000)
        self.fs.remove_base_sensors()
        nvml.fail = True                                               # Xid 79: the GPU has fallen off the bus
        os.remove(self.fs.path(f"sys/bus/pci/devices/{PCI}/power/runtime_status"))
        self.cycles(d, 4000, 80000, 2000)
        self.assertEqual(d.state, "control")
        self.assertTrue(d.dgpu.enabled)                                # gone is no failure: it works again after the re-attach
        self.assertEqual((self.status()["dgpu"]["gate"], self.status()["dgpu"]["cap_mhz"]), ("gone", None))
        d.stop()


if __name__ == "__main__":
    unittest.main()
