"""Integration tests for the daemon on a fake sysfs with a synthetic blob (spec supervisor 1, 9; monitoring 2; AC-S1, AC-S8)."""
import io
import json
import os
import tempfile
import unittest

from fakesys import FakeSys
from helpers import LIMITS, POWER, SYSTEM, TEMP, VN, BlobBuilder, G

from surface_power import daemon, monitor

PL1 = "sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"
PL2 = "sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw"


def blob_text(absent_sensor=False, absent_on_written=False, extra=(), node_controller=False):
    b = BlobBuilder()
    b.input_channel(TEMP, 509, 3681, 2731)
    b.output_channel(LIMITS, 51, 25000, 31000, 7500)
    b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 7, 15, 4, 0], divider=1)
    for st in range(7):
        b.line(G["pid_parameter_set"], 1, [], [], [1, st, st, 30000, 30001, 7500, 21000, 3211, 10, 40, 100, 0.4])
    b.line(G["lookup_table"], 2, [(SYSTEM, 1)], [(VN, 2)], [6, 25000, 25000, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6,
                                                            25000, 13000, 25000, 25000, 30000, 25000])
    b.line(G["min_max"], 3, [(VN, 1), (VN, 2)], [(LIMITS, 51)], [30000, 8500, 15000, 0], stage=1)
    b.line(G["lookup_table"], 4, [(SYSTEM, 1)], [(LIMITS, 52)], [6, 25000, 25000, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6,
                                                                 25000, 15000, 40000, 25000, 61000, 35000])
    if absent_sensor:
        # A second controller on TEMP/609, which the fake system does not provide; it feeds a log-only output (dGPU 201).
        b.line(G["pid_sets"], 10, [(TEMP, 609), (SYSTEM, 1)], [(VN, 10)], [20, 1, 15, 4, 0], divider=1)
        b.line(G["pid_parameter_set"], 10, [], [], [10, 0, 7, 50000, 50001, 20000, 40000, 3181, 10, 40, 100, 0.4])
        b.line(G["pass_through"], 11, [(VN, 10)], [(LIMITS, 201)], [50000], stage=1)
    if node_controller:
        # A controller whose input is an internal node (Pro 10: VN470, a smoothed battery current), here a copy of 509.
        b.line(G["pass_through"], 20, [(TEMP, 509)], [(VN, 20)], [3000], stage=1)
        b.line(G["pid_sets"], 21, [(VN, 20), (SYSTEM, 1)], [(VN, 21)], [20, 1, 15, 4, 0], divider=1, stage=2)
        b.line(G["pid_parameter_set"], 21, [], [], [21, 0, 7, 50000, 50001, 20000, 40000, 3181, 10, 40, 100, 0.4])
        b.line(G["pass_through"], 22, [(VN, 21)], [(LIMITS, 201)], [50000], stage=3)
    for line in extra:
        b.algorithms.append(line)
    if absent_on_written:
        # TEMP/608 is not provided either, but here it feeds psys PL1, which is written on Linux.
        b.line(G["pass_through"], 12, [(TEMP, 608)], [(LIMITS, 54)], [45000], stage=1)
    return b.text()


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.run = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.run.name, "blob.txt")
        with open(self.blob, "w") as f:
            f.write(blob_text())
        self.fs.write("sys/class/hwmon/hwmon2/temp9_input", 30000)

    def tearDown(self):
        self.fs.cleanup()
        self.run.cleanup()
        self.state.cleanup()

    def start(self, *extra):
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name,
                "--no-ec", *extra]
        return daemon.Daemon(daemon.parse_args(args), monitor.Journal(stream=io.StringIO()))

    def status(self):
        with open(os.path.join(self.run.name, "status.json")) as f:
            return json.load(f)

    def test_dry_run_writes_nothing(self):
        d = self.start("--dry-run")
        for t in range(5):
            d.cycle(t * 1000)
        self.assertEqual(self.fs.read(PL1), "30000000")
        st = self.status()
        self.assertEqual(st["state"], "dry-run")
        pl1 = next(o for o in st["outputs"] if o["name"] == "PL1")
        self.assertEqual((pl1["value_w"], pl1["target"]), (30.0, "dry-run"))
        d.stop()

    def test_untested_device_defaults_to_dry_run(self):
        d = self.start()
        self.assertEqual(d.mode, "dry-run")
        d.stop()

    def test_control_writes_and_restores(self):
        d = self.start("--control")
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        self.fs.write("sys/class/power_supply/ADP1/online", 0)       # balanced on battery: state 2 -> 13/15 W
        d.cycle(0)
        self.assertEqual(self.fs.read(PL1), "13000000")
        self.assertEqual(self.fs.read(PL2), "15000000")
        self.assertTrue(os.path.exists(os.path.join(self.run.name, "snapshot.json")))
        d.stop()
        self.assertEqual(self.fs.read(PL1), "30000000")              # AC-S1
        self.assertEqual(self.fs.read(PL2), "61000000")
        self.assertFalse(os.path.exists(os.path.join(self.run.name, "snapshot.json")))

    def test_foreign_changes_make_it_yield(self):
        d = self.start("--control")
        d.cycle(0)
        for t in (1000, 2000, 3000):
            self.fs.write(PL1, 12000000)
            d.cycle(t)
        self.assertEqual(d.state, "yielded")
        self.assertEqual(self.fs.read(PL1), "30000000")              # firmware value once
        self.fs.write(PL1, 12000000)
        d.cycle(4000)
        self.assertEqual(self.fs.read(PL1), "12000000")              # then hands off
        d.stop()

    def test_absent_controller_input_is_no_emergency(self):
        """A controller on a sensor this system never provides (SB3: base sensor 609) stays OFF; that is logged, not an emergency."""
        with open(self.blob, "w") as f:
            f.write(blob_text(absent_sensor=True))
        d = self.start("--control", "--allow-partial-outputs")     # the dGPU limit loses its stand-in here
        self.assertIn(daemon.ChannelRef(TEMP, 609), d.reader.unavailable)
        for t in range(0, 70000, 1000):
            d.cycle(t)
        self.assertEqual(d.state, "control")
        # The SB3 profile enables the dGPU stand-in, but its input is missing: only the stand-in goes off.
        self.assertIsNone(d.dgpu)
        self.assertIn("2E8B14C2/609", d.dgpu_note)
        d.stop()

    def test_absent_input_on_a_written_output_forces_dry_run(self):
        """An input missing from the start that reaches a written limit: no control, only dry-run (spec supervisor 2)."""
        with open(self.blob, "w") as f:
            f.write(blob_text(absent_sensor=True, absent_on_written=True))
        d = self.start("--control")
        self.assertIn(daemon.ChannelRef(LIMITS, 54), d.outputs.targets)    # psys PL1 has a Linux target
        self.assertEqual(d.mode, "dry-run")
        d.cycle(0)
        self.assertEqual(self.fs.read(PL1), "30000000")                     # nothing written
        self.assertIn("2E8B14C2/608", d.mode_reason)
        d.stop()

    def test_lost_controller_input_is_an_emergency(self):
        """A sensor that was there and disappears still counts as lost after 60 s (spec supervisor 4)."""
        d = self.start("--control")
        d.cycle(0)
        os.remove(self.fs.path("sys/class/hwmon/hwmon2/temp9_input"))
        for t in range(1000, 62000, 1000):
            d.cycle(t)
        self.assertEqual(d.state, "emergency")
        d.stop()

    def test_output_without_linux_path_forces_dry_run(self):
        """Control only when every output has a Linux path (spec supervisor 2.6): PL4 off, and an unknown output."""
        self.fs.write("sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw", 0)
        with open(self.blob, "w") as f:
            f.write(blob_text(extra=["1,0,0,026CCFCC-A52A-4DB1-8B49-D2BAAFCAD329,30,1,1,2DD6960F-7CB9-4BB8-82BF-97FA5D8BD767,1,"
                                     "B620798F-6FB0-42EF-B9B3-D2370402E089,201,4"]))
        d = self.start("--control")
        self.assertEqual(d.mode, "dry-run")
        self.assertIn("B620798F", d.mode_reason)
        d.stop()
        d = self.start("--control", "--allow-partial-outputs")
        self.assertEqual(d.mode, "control")
        d.stop()

    def test_thermal_actions_fire_only_in_control(self):
        """A blob that requests hibernate (O_HOT = 2): control runs the action once, dry-run only logs it."""
        hot = ["1,0,0,026CCFCC-A52A-4DB1-8B49-D2BAAFCAD329,31,1,1,99999999-9999-9999-9999-999999999999,1,"
               "110DF2A4-5995-473C-92FD-3CAC40EE2E6C,1,2"]          # placeholder input -> the default 2
        with open(self.blob, "w") as f:
            f.write(blob_text(extra=hot))
        for flag, expected in (("--control", ["systemctl hibernate"]), ("--dry-run", [])):
            calls = []
            d = self.start(flag)
            d.actions.runner = lambda cmd: (calls.append(" ".join(cmd)), True)[1]
            for t in range(0, 12000, 1000):
                d.cycle(t)
            self.assertEqual(calls, expected, flag)
            self.assertIn("thermal_actions", self.status())
            d.stop()

    def test_pl4_is_held_while_its_ec_input_is_invalid(self):
        """PL4 from the available input power: without EC data (--no-ec) it keeps the firmware value (spec outputs 3.1)."""
        pl4 = ["1,0,0,026CCFCC-A52A-4DB1-8B49-D2BAAFCAD329,32,1,1,28B7BD85-B4CD-49F1-B02B-F84E5AA09889,8,"
               "0B7B5350-BB8C-4459-8D4A-A75C57073801,53,20000"]
        with open(self.blob, "w") as f:
            f.write(blob_text(extra=pl4))
        PL4 = "sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw"
        self.fs.write("sys/class/dmi/id/product_name", "Surface Pro 9")     # no profile: PL4 automatic, control without EC data
        d = self.start("--control", "--allow-partial-outputs")
        for t in range(0, 6000, 1000):
            d.cycle(t)
        self.assertEqual(self.fs.read(PL4), "100000000")                    # firmware value kept, not the 20 W fallback
        out = {o["channel"]: o for o in self.status()["outputs"]}
        self.assertEqual(out["0B7B5350/53"]["target"], "held at firmware value (EC inputs invalid)")
        self.assertEqual(out["0B7B5350/53"]["value_w"], 100.0)              # what stays in place, not the 20 W fallback
        self.assertEqual(out["0B7B5350/53"]["computed_w"], 20.0)
        d.stop()

    # --- PL4 on the Pro 10: never the blob fallback, and no control without EC data on its path ---
    PL4_BLOB = ["1,0,0,026CCFCC-A52A-4DB1-8B49-D2BAAFCAD329,32,1,1,28B7BD85-B4CD-49F1-B02B-F84E5AA09889,8,"
                "0B7B5350-BB8C-4459-8D4A-A75C57073801,53,20000"]
    PL4_PATH = "sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw"

    class Ec:
        """EC transport: answers 0x53 with an available input power of 73.5 W; `fail` makes every request time out."""

        def __init__(self):
            self.fail = False
            self.calls = 0

        def request(self, tid, cid, iid=1):
            import struct
            self.calls += 1
            if self.fail or cid != 0x53 or tid != 1:
                return None
            return bytes([1]) + struct.pack("<IIHHII", 47900, 35600, 170, 8030, 73500, 9700)

    def pl4_start(self, ec, *extra, product="Surface Pro 10 for Business", on_nap=None, journal=None):
        """Start on a fake clock that begins at 0 and advances only through the daemon's waiting; `on_nap(n)` runs per nap."""
        with open(self.blob, "w") as f:
            f.write(blob_text(extra=self.PL4_BLOB))
        self.fs.write("sys/class/dmi/id/product_name", product)
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name, *extra]
        now = [0.0]
        self.naps = []

        def nap(s):
            self.naps.append(s)
            now[0] += s
            if on_nap:
                on_nap(len(self.naps))
        old = daemon._sleep, daemon._clock
        daemon._sleep, daemon._clock = nap, lambda: now[0]
        try:
            return daemon.Daemon(daemon.parse_args(args), journal or monitor.Journal(stream=io.StringIO()), ec_transport=ec)
        finally:
            daemon._sleep, daemon._clock = old

    def test_pl4_never_gets_the_fallback_when_the_ec_stops_answering(self):
        """Valid EC data write PL4; if the EC stops answering (also after a resume, the gate closed) the written value stays."""
        ec = self.Ec()
        d = self.pl4_start(ec, "--control", "--allow-partial-outputs", product="Surface Pro 9")   # no profile: only the hold
        d.cycle(0)
        self.assertEqual(self.fs.read(self.PL4_PATH), "73500000")           # computed from the EC data
        ec.fail = True
        written = []
        for t in range(1000, 400000, 1000):                                  # refreshes at 60 s fail three times: gate closed
            d.cycle(t)
            written.append(self.fs.read(self.PL4_PATH))
        self.assertEqual(set(written), {"73500000"})                         # never the 20 W fallback
        self.assertIsNone(d.values[daemon.ChannelRef(POWER, 8)])
        d.before_sleep()                                                     # the firmware values are back while it sleeps
        d.after_resume(400000)                                               # reopens the gate; the EC is still silent
        for t in range(400000, 470000, 1000):
            d.cycle(t)
            self.assertEqual(self.fs.read(self.PL4_PATH), "100000000")       # still the firmware value, not the 20 W fallback
        out = {o["channel"]: o for o in self.status()["outputs"]}
        self.assertEqual(out["0B7B5350/53"]["target"], "held at firmware value (EC inputs invalid)")
        ec.fail = False                                                      # the EC is back after the next resume
        d.before_sleep()
        d.after_resume(470000)
        d.cycle(471000)
        self.assertEqual(self.fs.read(self.PL4_PATH), "73500000")
        d.stop()
        self.assertEqual(self.fs.read(self.PL4_PATH), "100000000")           # firmware value restored

    def test_pl4_not_written_before_the_first_valid_ec_answer(self):
        """The EC answers only after a while: nothing is written to PL4 before that, and the fallback never."""
        ec = self.Ec()
        ec.fail = True
        d = self.pl4_start(ec, "--control", "--allow-partial-outputs", product="Surface Pro 9")
        for t in range(0, 5000, 1000):
            d.cycle(t)
            self.assertEqual(self.fs.read(self.PL4_PATH), "100000000")
        ec.fail = False
        d.platform.request_refresh()
        d.cycle(5000)
        self.assertEqual(self.fs.read(self.PL4_PATH), "73500000")
        d.stop()

    def test_pl4_requires_ec_refuses_control_without_ec_data(self):
        """Profile option pl4_requires_ec: no valid EC input on the PL4 path within the wait -> dry-run only, with the reason."""
        ec = self.Ec()
        ec.fail = True
        d = self.pl4_start(ec, "--control", "--allow-partial-outputs")
        self.assertEqual((d.mode, d.state), ("dry-run", "dry-run"))
        self.assertIn("PL4 needs valid EC inputs", d.mode_reason)
        self.assertIn("28B7BD85/8", d.mode_reason)
        self.assertIn("within 10 s", d.mode_reason)
        self.assertGreaterEqual(sum(self.naps), 10)                          # it did wait the 10 s
        self.assertIsNone(d.snapshot)
        d.cycle(0)
        self.assertEqual(self.fs.read(self.PL4_PATH), "100000000")
        self.assertEqual(self.fs.read(PL1), "30000000")                      # nothing written at all
        d.stop()

    def test_pl4_requires_ec_allows_control_with_ec_data(self):
        ec = self.Ec()
        d = self.pl4_start(ec, "--control", "--allow-partial-outputs")
        self.assertEqual(d.mode, "control")
        self.assertEqual(d.mode_reason, "")
        d.cycle(0)
        self.assertEqual(self.fs.read(self.PL4_PATH), "73500000")
        d.stop()

    def test_pl4_requires_ec_waits_for_a_late_answer(self):
        """The EC answers on the third poll, within the wait: control starts."""
        ec = self.Ec()
        ec.fail = True

        def nap(n):
            if n == 2:
                ec.fail = False
        d = self.pl4_start(ec, "--control", "--allow-partial-outputs", on_nap=nap)
        self.assertEqual(d.mode, "control")
        self.assertLess(sum(self.naps), 10)
        d.stop()

    def test_pl4_requires_ec_is_off_for_other_profiles_and_in_dry_run(self):
        ec = self.Ec()
        ec.fail = True
        d = self.pl4_start(ec, "--control", "--allow-partial-outputs", product="Surface Book 3")
        self.assertEqual(d.mode, "control")                                  # no such option in this profile: the hold alone
        d.cycle(0)
        self.assertEqual(self.fs.read(self.PL4_PATH), "100000000")
        d.stop()
        d = self.pl4_start(ec, "--dry-run")
        self.assertEqual(d.mode_reason, "")
        d.stop()

    def test_pro10_profile_note_in_the_log(self):
        ec = self.Ec()
        buf = io.StringIO()
        d = self.pl4_start(ec, "--dry-run", journal=monitor.Journal(stream=buf))
        self.assertIn("integrated GPU", buf.getvalue())
        d.stop()

    def test_controller_on_an_internal_node_is_no_emergency(self):
        """Internal nodes are not device inputs; their validity follows the device inputs (seen on a Pro 10 report)."""
        with open(self.blob, "w") as f:
            f.write(blob_text(node_controller=True))
        d = self.start("--control")
        for t in range(0, 70000, 1000):
            d.cycle(t)
        self.assertEqual(d.state, "control")
        d.stop()

    def test_suspend_and_resume(self):
        d = self.start("--control")
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        d.cycle(0)
        d.before_sleep()
        self.assertEqual(self.fs.read(PL1), "30000000")
        d.cycle(1000)                                                  # paused: nothing written
        self.assertEqual(self.fs.read(PL1), "30000000")
        d.after_resume(2000)
        d.cycle(2000)
        self.assertEqual(self.fs.read(PL1), "25000000")
        d.stop()

    def test_dgpu_stand_in(self):
        """DO-1 (spec outputs 6): opt-in clock cap on the blob's dGPU limit; released on stop and in emergency."""
        from test_gpu import PCI, FakeNvml
        b = BlobBuilder()
        b.line(G["lookup_table"], 20, [(SYSTEM, 1)], [(LIMITS, 201)], [6, 50000, 50000, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6,
                                                                     50000, 50000, 50000, 50000, 50000, 50000])
        with open(self.blob, "w") as f:
            f.write(blob_text(extra=b.algorithms))                       # dGPU limit 201 = 50 W in every slider state
        self.fs.write(f"sys/bus/pci/devices/{PCI}/vendor", "0x10de")
        self.fs.write(f"sys/bus/pci/devices/{PCI}/class", "0x030200")
        self.fs.link_raw("proc/4242/fd/7", "/dev/nvidia0")             # another client keeps the GPU awake
        nvml = FakeNvml()
        d = self.start("--control", "--dgpu-clock-cap")
        d.dgpu.factory = lambda pci: nvml
        d.cycle(0)
        self.assertEqual(nvml.cap, 1260)
        self.assertEqual(self.status()["dgpu"]["cap_mhz"], 1260)
        out = {o["channel"]: o for o in self.status()["outputs"]}
        self.assertEqual(out["0B7B5350/201"]["target"], "clock-cap stand-in")
        os.remove(self.fs.path("sys/class/hwmon/hwmon2/temp9_input"))   # skin sensor lost -> emergency after 60 s
        for t in range(2000, 64000, 2000):
            d.cycle(t)
        self.assertEqual(d.state, "emergency")
        self.assertIsNone(nvml.cap)
        d.stop()

    def test_no_dgpu_stand_in_without_a_dgpu_output(self):
        d = self.start("--control")
        self.assertIsNone(d.dgpu)
        d.stop()

    def test_missing_ssam_cdev_is_reported(self):
        """Without the SSAM debug device the EC power data stay invalid; the daemon must say so at start (NFR-11)."""
        b = BlobBuilder()
        b.line(G["pass_through"], 1, [(POWER, 8)], [(LIMITS, 201)], [50000], stage=1)   # reads the available input power
        with open(self.blob, "w") as f:
            f.write(b.text())
        out = io.StringIO()
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name,
                "--dry-run"]
        d = daemon.Daemon(daemon.parse_args(args), monitor.Journal(stream=out))
        self.assertIn("surface_aggregator_cdev", out.getvalue())
        d.stop()

    def test_control_refuses_while_thermald_runs(self):
        """FR-S6: thermald changes PL1 itself, so control does not start while it runs; dry-run may run next to it."""
        self.fs.write("proc/4242/comm", "thermald")
        with self.assertRaises(daemon.Idle):
            self.start("--control")
        self.assertEqual(self.fs.read(PL1), "30000000")
        d = self.start("--dry-run")
        self.assertEqual(d.mode, "dry-run")
        d.stop()

    def test_warns_when_a_limit_writer_runs(self):
        """A dry-run next to thermald, tuned or power-profiles-daemon: say so, the measured limits are not the computed ones."""
        for pid, comm in ((4242, "thermald"), (4243, "power-profiles-")):
            self.fs.write(f"proc/{pid}/comm", comm)
        out = io.StringIO()
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name,
                "--no-ec", "--dry-run"]
        d = daemon.Daemon(daemon.parse_args(args), monitor.Journal(stream=out))
        d.cycle(0)
        self.assertIn("power-profiles-daemon, thermald running", out.getvalue())
        self.assertEqual(self.status()["competing_writers"], ["power-profiles-daemon", "thermald"])
        d.stop()

    def test_unsupported_device_exits_without_writes(self):  # AC-S8
        self.fs.write("sys/class/dmi/id/sys_vendor", "LENOVO")
        with self.assertRaises(daemon.Idle):
            self.start("--control")
        self.assertEqual(self.fs.read(PL1), "30000000")

    def test_time_series(self):
        d = self.start("--dry-run")
        for t in range(3):
            d.cycle(t * 1000)
        d.stop()
        files = [f for f in os.listdir(self.state.name) if f.startswith("series-")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.state.name, files[0])) as f:
            header = f.readline().strip().split(",")
        self.assertEqual(header[:2], ["t_ms", "kind"])
        self.assertIn("2E8B14C2/509", header)


if __name__ == "__main__":
    unittest.main()
