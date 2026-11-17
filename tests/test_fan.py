"""Fan and charging outputs B620798F (spec outputs 9) and scripts/fan-test.py, all on fakes: no device is opened."""
import contextlib
import importlib.util
import io
import json
import os
import signal
import struct
import tempfile
import time
import unittest
from unittest import mock

from fakesys import FakeSys
from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G

from surface_power import daemon, monitor, supervisor
from surface_power.adapters import fan
from surface_power.adapters.fan import FAN_GUID, FanStage, Frame
from surface_power.loader.model import ChannelRef, OutputChannel
from surface_power.tools import report as report_tool
from surface_power.tools import reset as reset_tool


def ref(ch):
    return ChannelRef(FAN_GUID, ch)


class FakeTransport:
    """Stands in for the SSAM debug device: records every request, can fail."""

    def __init__(self, fail=False, root_needed=False):
        self.fail, self.root_needed = fail, root_needed
        self.sent: list[Frame] = []
        self.reads: list[tuple] = []
        self.answers: dict = {}
        self.path = "/fake/aggregator"

    def can_write(self):
        return not self.root_needed

    def needs_root(self):
        return self.root_needed

    def send(self, tc, cid, iid, payload, tid=1):
        if self.fail:
            return False
        self.sent.append(Frame(tc, iid, cid, bytes(payload), tid))
        return True

    def read(self, tc, cid, iid=1, tid=1):
        self.reads.append((tc, cid, iid))
        return self.answers.get((tc, cid, iid))


def rng(lo, hi):
    return OutputChannel(1, ChannelRef(FAN_GUID, 0), 0, hi, lo)


class FrameTest(unittest.TestCase):
    def frames(self, ch, value, **kw):
        t = FakeTransport()
        st = FanStage("/nonexistent", [ref(ch)], transport=t, **kw)
        st.apply({ref(ch): value}, 0)
        return t.sent

    def test_wire_layout(self):
        f = Frame(fan.TC_FAN, 1, 0x0B, struct.pack("<H", 3000))
        self.assertEqual(f.wire(), bytes([5, 1, 1, 0, 0x0B, 2, 0]) + struct.pack("<H", 3000))   # nine bytes for a u16

    def test_payload_per_channel(self):
        cases = [(101, 3000, 0x0B, 1, struct.pack("<H", 3000)),
                 (103, 2500, 0x0B, 3, struct.pack("<H", 2500)),
                 (111, 25, 0x0C, 1, struct.pack("<f", 25)),
                 (123, 18, 0x12, 3, struct.pack("<f", 18)),
                 (114, 8, 0x15, 1, bytes([8])),
                 (125, 3, 0x14, 2, bytes([3])),
                 (306, 40, 0x1C, 1, struct.pack("<f", 40)),
                 (312, 12, 0x16, 1, bytes([12]))]
        for ch, v, cid, iid, pl in cases:
            with self.subTest(ch=ch):
                (f,) = self.frames(ch, v)
                self.assertEqual((f.tc, f.tid, f.iid, f.cid, f.flags, f.payload), (5, 1, iid, cid, 0, pl))

    def test_temperature_frames_when_opted_in(self):
        for ch, tc, cid, v in ((302, 5, 0x19, 3161), (304, 5, 0x1A, 3131), (305, 5, 0x1B, 3201), (307, 5, 0x1D, 3281),
                               (308, 3, 0x25, 3301), (309, 3, 0x26, 3201), (310, 3, 0x27, 3251), (311, 3, 0x28, 3281)):
            with self.subTest(ch=ch):
                (f,) = self.frames(ch, v, thresholds=True)
                self.assertEqual((f.tc, f.iid, f.cid, f.payload), (tc, 1, cid, struct.pack("<H", v)))

    def test_clamped_to_the_blob_range(self):
        t = FakeTransport()
        st = FanStage("/nonexistent", [ref(101), ref(124)], {ref(101): rng(0, 5000), ref(124): rng(0, 8)}, transport=t)
        st.apply({ref(101): 9000, ref(124): 40}, 0)
        by = {f.cid: f for f in t.sent}
        self.assertEqual(by[0x0B].payload, struct.pack("<H", 5000))
        self.assertEqual(by[0x14].payload, bytes([8]))

    def test_hard_bound_without_blob_range(self):
        (f,) = self.frames(101, 99999)
        self.assertEqual(f.payload, struct.pack("<H", 10000))
        (f,) = self.frames(111, 500)
        self.assertEqual(f.payload, struct.pack("<f", 100))

    def test_neutral_frames(self):
        self.assertEqual(fan.neutral_frame(101).payload, b"\x00\x00")
        self.assertEqual(fan.neutral_frame(111).payload, b"\x00\x00\x00\x00")
        self.assertEqual(fan.neutral_frame(114).payload, b"\x00")
        self.assertEqual(fan.neutral_frame(121).cid, 0x12)
        for ch in (201, 301, 302, 308, 311):
            self.assertIsNone(fan.neutral_frame(ch))


class SendRulesTest(unittest.TestCase):
    def setUp(self):
        self.t = FakeTransport()
        self.st = FanStage("/nonexistent", [ref(101), ref(121)], transport=self.t)

    def test_first_cycle_sends_and_unchanged_does_not(self):
        r = self.st.apply({ref(101): 3000, ref(121): 20}, 0)
        self.assertEqual(len(self.t.sent), 2)
        self.assertTrue(r[ref(101)].written)
        self.st.apply({ref(101): 3000, ref(121): 20}, 10000)
        self.assertEqual(len(self.t.sent), 2)

    def test_dead_band_100_rpm_and_1_otherwise(self):
        self.st.apply({ref(101): 3000, ref(121): 20}, 0)
        self.st.apply({ref(101): 3090, ref(121): 21}, 10000)         # 90 rpm: inside the band; SPL 1: outside
        self.assertEqual([f.cid for f in self.t.sent], [0x0B, 0x12, 0x12])
        self.st.apply({ref(101): 3100, ref(121): 21}, 20000)         # 100 rpm away from the last sent value
        self.assertEqual([f.cid for f in self.t.sent][-1], 0x0B)
        self.assertEqual(self.t.sent[-1].payload, struct.pack("<H", 3100))

    def test_at_most_one_send_per_channel_per_5_s(self):
        self.st.apply({ref(121): 20}, 0)
        self.st.apply({ref(121): 25}, 1000)
        self.st.apply({ref(121): 25}, 4999)
        self.assertEqual(len(self.t.sent), 1)
        self.st.apply({ref(121): 25}, 5000)
        self.assertEqual(len(self.t.sent), 2)
        self.assertEqual(self.t.sent[1].payload, struct.pack("<f", 25))

    def test_resend_after_resume(self):
        self.st.apply({ref(121): 20}, 0)
        self.st.forget()
        self.st.apply({ref(121): 20}, 1000)
        self.assertEqual(len(self.t.sent), 2)

    def test_failed_send_is_retried_then_log_only(self):
        self.t.fail = True
        for ms, n in ((0, 1), (1000, 1), (5000, 2), (10000, 3)):
            self.st.apply({ref(121): 20}, ms)
        self.assertTrue(self.st.chans[ref(121)].off)
        self.assertFalse(self.st.has_path(ref(121)))
        self.assertIn("no Linux path", self.st.describe(ref(121)))
        self.assertTrue(any("log-only" in t for _, t in self.st.drain_events()))
        self.assertTrue(any("log-only from now on" in n for n in self.st.notes))
        self.t.fail = False
        r = self.st.apply({ref(121): 20}, 20000)
        self.assertEqual((r[ref(121)].kind, self.t.sent), ("log-only", []))

    def test_failure_then_success_recovers(self):
        self.t.fail = True
        self.st.apply({ref(121): 20}, 0)
        self.t.fail = False
        r = self.st.apply({ref(121): 20}, 5000)
        self.assertTrue(r[ref(121)].written)
        self.assertEqual(self.st.chans[ref(121)].fails, 0)

    def test_dry_run_sends_nothing(self):
        st = FanStage("/nonexistent", [ref(101)], transport=self.t, dry_run=True)
        r = st.apply({ref(101): 3000}, 0)
        self.assertEqual((r[ref(101)].kind, self.t.sent), ("dry-run", []))
        self.assertEqual(st.status()[0]["would"], Frame(5, 1, 0x0B, struct.pack("<H", 3000)).wire().hex())
        self.assertTrue(st.has_path(ref(101)))

    def test_touch_is_recorded_before_the_send(self):
        order = []
        st = FanStage("/nonexistent", [ref(101)], transport=self.t, on_touch=lambda k, f: order.append(("touch", k, f)))
        self.t.sent = order                       # the transport appends to the same list
        st.apply({ref(101): 3000}, 0)
        self.assertEqual(order[0][:2], ("touch", "fan:101"))
        self.assertEqual(order[0][2].payload, b"\x00\x00")
        self.assertIsInstance(order[1], Frame)

    def test_no_send_if_the_restore_frame_cannot_be_recorded(self):
        def boom(k, f):
            raise OSError("disk full")
        st = FanStage("/nonexistent", [ref(101)], transport=self.t, on_touch=boom)
        r = st.apply({ref(101): 3000}, 0)
        self.assertEqual((self.t.sent, r[ref(101)].written), ([], False))


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.t = FakeTransport()

    def tearDown(self):
        self.fs.cleanup()

    def bind_profile_driver(self, fan_hwmon=False):
        d = self.fs.path("sys/bus/surface_aggregator/drivers/surface_platform_profile")
        os.makedirs(os.path.join(self.fs.root, "sys/devices/01:00:01:00:01"), exist_ok=True)
        os.makedirs(d, exist_ok=True)
        os.symlink(os.path.join(self.fs.root, "sys/devices/01:00:01:00:01"), os.path.join(d, "01:00:01:00:01"))
        self.fs.write(os.path.join("sys/bus/surface_aggregator/drivers/surface_platform_profile", "uevent"), "")
        if fan_hwmon:
            self.fs.write("sys/class/hwmon/hwmon5/name", "surface_fan")

    def stage(self, chans, **kw):
        return FanStage(self.fs.root, [ref(c) for c in chans], transport=self.t, **kw)

    def test_sam_machine_without_usable_device(self):
        """No root or no cdev module on a SAM machine: the path exists, it is not usable now; ACPI-EC machines have none."""
        self.t = FakeTransport(root_needed=False)
        self.t.can_write = lambda: False                       # node missing
        st = self.stage([101])
        self.assertEqual(st.describe(ref(101)), "no Linux path")
        self.assertFalse(st.needs_cdev(ref(101)))
        self.fs.write("sys/bus/surface_aggregator/devices/01:0b:01:00:01/modalias", "x")
        st = self.stage([101])
        self.assertIn("needs root and surface_aggregator_cdev", st.describe(ref(101)))
        self.assertTrue(st.needs_cdev(ref(101)))
        self.assertFalse(st.has_path(ref(101), report=True))
        self.assertEqual(st.apply({ref(101): 50}, 0)[ref(101)].kind, "log-only")

    def test_kernel_path_detected(self):
        self.assertFalse(fan.kernel_path(self.fs.root).bound)
        self.bind_profile_driver(fan_hwmon=True)
        k = fan.kernel_path(self.fs.root)
        self.assertEqual((k.bound, k.fan_seen), (True, True))

    def test_profile_and_state_are_written_by_the_kernel(self):
        self.bind_profile_driver()
        st = self.stage([201, 301, 121])
        self.assertEqual((st.describe(ref(201)), st.describe(ref(301))), ("kernel (platform profile)", "kernel (suspend)"))
        self.assertTrue(st.has_path(ref(201)) and st.has_path(ref(301)))
        self.assertFalse(st.sends(ref(201)))
        r = st.apply({ref(201): 4, ref(301): 2, ref(121): 20}, 0)
        self.assertEqual([f.cid for f in self.t.sent], [0x12])          # neither 0x0e nor 0x0f from the daemon
        self.assertEqual((r[ref(201)].kind, r[ref(301)].kind), ("kernel", "kernel"))

    def test_without_the_kernel_driver_there_is_no_path_and_still_no_send(self):
        st = self.stage([201, 301])
        self.assertEqual((st.describe(ref(201)), st.describe(ref(301))), ("no Linux path",) * 2)
        self.assertFalse(st.has_path(ref(201)))
        st.apply({ref(201): 3, ref(301): 2}, 0)
        self.assertEqual(self.t.sent, [])

    def test_fan_2_and_3_profile_have_no_path(self):
        self.bind_profile_driver()
        st = self.stage([202])
        self.assertFalse(st.has_path(ref(202)))

    def test_fan_2_profile_is_the_kernels_where_the_device_profile_says_so(self):
        self.bind_profile_driver()
        st = self.stage([201, 202, 203], kernel_fan_profiles=2)
        self.assertEqual((st.describe(ref(201)), st.describe(ref(202)), st.describe(ref(203))),
                         ("kernel (platform profile)", "kernel (platform profile, fan 2)", "no Linux path"))
        self.assertTrue(st.has_path(ref(202)))
        self.assertFalse(st.has_path(ref(203)))
        self.assertFalse(st.sends(ref(202)))
        r = st.apply({ref(201): 3, ref(202): 3, ref(203): 3}, 0)
        self.assertEqual((r[ref(202)].kind, r[ref(203)].kind), ("kernel", "log-only"))
        self.assertEqual(self.t.sent, [])                                 # the daemon never sends 0x0e itself
        self.assertTrue(any("2 fans" in n for n in st.notes))
        self.assertEqual(self.stage([203], kernel_fan_profiles=3).describe(ref(203)), "kernel (platform profile, fan 3)")
        self.assertEqual(self.stage([203], kernel_fan_profiles=9).kernel_fan_profiles, 3)     # clamped to the three fan instances

    def test_default_is_fan_1_only(self):
        self.bind_profile_driver()
        st = self.stage([201, 202])
        self.assertEqual((st.describe(ref(201)), st.describe(ref(202))), ("kernel (platform profile)", "no Linux path"))
        self.assertFalse(any("fans" in n and "fan_count" in n for n in st.notes))

    def test_fan_2_needs_the_bound_driver_too(self):
        st = self.stage([201, 202], kernel_fan_profiles=2)
        self.assertEqual((st.describe(ref(201)), st.describe(ref(202))), ("no Linux path", "no Linux path"))

    def test_log_for_fan_2_names_the_fan(self):
        self.bind_profile_driver()
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        st = self.stage([201, 202], kernel_fan_profiles=2)
        st.apply({ref(201): 4, ref(202): 2}, 0)
        ev = [t for _, t in st.drain_events()]
        self.assertEqual(len(ev), 1)
        self.assertIn("asks for 4", ev[0])
        st.apply({ref(202): 4}, 1000)
        ev = [t for _, t in st.drain_events()]
        self.assertEqual(len(ev), 1)
        self.assertIn("fan profile (fan 2): the blob asks for 4", ev[0])

    def test_log_when_the_blob_differs_from_the_kernel(self):
        self.bind_profile_driver()
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        st = self.stage([201, 301])
        st.apply({ref(201): 4, ref(301): 2}, 0)
        st.apply({ref(201): 4, ref(301): 2}, 1000)                       # logged once
        ev = [t for _, t in st.drain_events()]
        self.assertEqual(len(ev), 2)
        self.assertIn("asks for 4, the kernel sends 2", ev[0])
        self.assertIn("never sends it", ev[1])
        st.apply({ref(201): 2}, 2000)                                    # same as the kernel: silent
        self.assertEqual(st.drain_events(), [])

    def test_surface_platform_profile_class_node_wins(self):
        self.fs.write("sys/class/platform-profile/platform-profile-0/name", "surface_platform_profile")
        self.fs.write("sys/class/platform-profile/platform-profile-0/profile", "performance")
        self.assertEqual(fan.platform_profile(self.fs.root), "performance")
        self.assertTrue(fan.kernel_path(self.fs.root).bound)

    def test_thresholds_are_opt_in(self):
        st = self.stage([302, 308, 306])
        self.assertEqual(st.describe(ref(302)), "disabled (profile opt-in)")
        self.assertFalse(st.has_path(ref(302)))
        r = st.apply({ref(302): 3161, ref(308): 3301, ref(306): 30}, 0)
        self.assertEqual([f.cid for f in self.t.sent], [0x1C])           # only 306 goes out
        self.assertEqual(r[ref(302)].kind, "disabled")
        on = self.stage([302, 308], thresholds=True)
        self.assertEqual((on.describe(ref(302)), on.describe(ref(308))), ("SAM FAN 0x19 (cdev)", "SAM TMP 0x25 (cdev)"))
        self.assertTrue(on.has_path(ref(302)))

    def test_no_device_no_path(self):
        t = FakeTransport(root_needed=True)
        st = FanStage(self.fs.root, [ref(101)], transport=t)
        self.assertEqual(st.describe(ref(101)), "SAM FAN 0x0b (cdev, needs root)")
        self.assertFalse(st.has_path(ref(101)))
        self.assertTrue(st.has_path(ref(101), report=True))
        st.apply({ref(101): 1000}, 0)
        self.assertEqual(t.sent, [])

    def test_cdev_missing_is_no_linux_path(self):
        class Missing(FakeTransport):
            def can_write(self):
                return False
        st = FanStage(self.fs.root, [ref(101)], transport=Missing())
        self.assertEqual(st.describe(ref(101)), "no Linux path")


class SnapshotFanTest(unittest.TestCase):
    def setUp(self):
        self.run = tempfile.TemporaryDirectory()
        self.t = FakeTransport()

    def tearDown(self):
        self.run.cleanup()

    def snap(self):
        s, _ = supervisor.Snapshot.take(self.run.name, [])
        s.transport = self.t
        return s

    def test_frames_recorded_before_send_and_sent_at_restore(self):
        s = self.snap()
        st = FanStage("/nonexistent", [ref(101), ref(124), ref(302)], transport=self.t, thresholds=True,
                      on_touch=lambda k, f: s.record_fan(k, f))
        st.apply({ref(101): 3000, ref(124): 3, ref(302): 3161}, 0)
        with open(os.path.join(self.run.name, "snapshot.json")) as f:
            data = json.load(f)
        self.assertEqual(sorted(data["fan"]["frames"]), ["fan:101", "fan:124"])
        self.assertEqual(data["fan"]["unrestorable"], ["fan:302"])
        self.t.sent.clear()
        self.assertEqual(s.restore(), [])
        self.assertEqual(sorted((f.cid, f.iid, f.payload) for f in self.t.sent),
                         [(0x0B, 1, b"\x00\x00"), (0x14, 1, b"\x00")])
        self.assertEqual(s.fan, {})
        self.t.sent.clear()
        self.assertEqual(s.restore(), [])
        self.assertEqual(self.t.sent, [])                          # sent once, not again

    def test_restore_failure_keeps_the_frame(self):
        s = self.snap()
        s.record_fan("fan:101", fan.neutral_frame(101))
        self.t.fail = True
        self.assertEqual(len(s.restore()), 1)
        self.assertIn("fan:101", s.fan)
        self.t.fail = False
        self.assertEqual(s.restore(), [])

    def test_reset_tool_sends_the_neutral_frames(self):
        s = self.snap()
        s.record_fan("fan:121", fan.neutral_frame(121))
        with mock.patch.object(supervisor, "CdevTransport", lambda path: self.t):
            self.assertEqual(reset_tool.main(["--run-dir", self.run.name]), 0)
        self.assertEqual([(f.cid, f.payload) for f in self.t.sent], [(0x12, b"\x00" * 4)])
        self.assertFalse(os.path.exists(os.path.join(self.run.name, "snapshot.json")))


# --- the daemon ---

def fan_blob(extra_outputs=(201, 301)):
    b = BlobBuilder()
    b.input_channel(TEMP, 509, 3681, 2731)
    b.output_channel(LIMITS, 51, 25000, 31000, 7500)
    b.output_channel(FAN_GUID, 121, 0, 100, 0)
    for c in extra_outputs:
        b.output_channel(FAN_GUID, c, 2, 4, 1)
    b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 7, 15, 4, 0], divider=1)
    for st in range(7):
        b.line(G["pid_parameter_set"], 1, [], [], [1, st, st, 30000, 30001, 7500, 21000, 3211, 10, 40, 100, 0.4])
    b.line(G["min_max"], 3, [(VN, 1), (VN, 1)], [(LIMITS, 51)], [30000, 8500, 15000, 0], stage=1)
    b.line(G["lookup_table"], 4, [(SYSTEM, 1)], [(FAN_GUID, 121)], [6, 25, 25, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6,
                                                                    25, 15, 40, 25, 61, 35], stage=1)
    for c in extra_outputs:
        b.line(G["pass_through"], 30 + c, [(SYSTEM, 1)], [(FAN_GUID, c)], [2], stage=1)
    return b.text()


class DaemonFanTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.run = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.run.name, "blob.txt")
        with open(self.blob, "w") as f:
            f.write(fan_blob())
        self.fs.write("sys/class/hwmon/hwmon2/temp9_input", 30000)
        self.t = FakeTransport()

    def tearDown(self):
        self.fs.cleanup()
        self.run.cleanup()
        self.state.cleanup()

    def start(self, *extra, transport="default", kernel=True):
        if kernel:
            d = self.fs.path("sys/bus/surface_aggregator/drivers/surface_platform_profile")
            os.makedirs(os.path.join(self.fs.root, "sys/devices/01:00:01:00:01"), exist_ok=True)
            os.makedirs(d, exist_ok=True)
            if not os.path.islink(os.path.join(d, "01:00:01:00:01")):
                os.symlink(os.path.join(self.fs.root, "sys/devices/01:00:01:00:01"), os.path.join(d, "01:00:01:00:01"))
        args = ["--root", self.fs.root, "--blob", self.blob, "--run-dir", self.run.name, "--state-dir", self.state.name, *extra]
        return daemon.Daemon(daemon.parse_args(args), monitor.Journal(stream=io.StringIO()),
                             fan_transport=self.t if transport == "default" else transport)

    def status(self):
        with open(os.path.join(self.run.name, "status.json")) as f:
            return json.load(f)

    def target(self, ch):
        return next(o["target"] for o in self.status()["outputs"] if o["channel"] == f"B620798F/{ch}")

    def test_dry_run_computes_frames_and_sends_nothing(self):
        d = self.start("--dry-run")
        d.cycle(0)
        self.assertEqual(self.t.sent, [])
        self.assertEqual(self.target(121), "would send SAM FAN 0x12 (cdev)")
        self.assertEqual((self.target(201), self.target(301)), ("kernel (platform profile)", "kernel (suspend)"))
        self.assertIsNotNone(next(f for f in self.status()["fan"] if f["channel"] == "B620798F/121")["would"])
        d.stop()

    def test_control_sends_records_and_restores(self):
        d = self.start("--control")
        self.assertEqual(d.unwritten, [])                          # every fan output has a path: SAM or kernel
        d.cycle(0)
        self.assertEqual([(f.cid, f.iid) for f in self.t.sent], [(0x12, 1)])
        self.assertEqual(self.target(121), "SAM FAN 0x12 (cdev)")
        with open(os.path.join(self.run.name, "snapshot.json")) as f:
            self.assertIn("fan:121", json.load(f)["fan"]["frames"])
        self.t.sent.clear()
        d.stop()
        self.assertEqual([(f.cid, f.payload) for f in self.t.sent], [(0x12, b"\x00" * 4)])
        self.assertFalse(os.path.exists(os.path.join(self.run.name, "snapshot.json")))

    def test_pause_emergency_and_yield_send_the_neutral_frames_and_resume_sends_again(self):
        d = self.start("--control")
        d.cycle(0)
        self.t.sent.clear()
        d.before_sleep()
        self.assertEqual([f.payload for f in self.t.sent], [b"\x00" * 4])
        d.after_resume(10000)
        self.t.sent.clear()
        d.cycle(10000)
        self.assertEqual([f.cid for f in self.t.sent], [0x12])      # resent on the first cycle after resume
        self.t.sent.clear()
        d._enter("emergency", "test")
        self.assertEqual([f.payload for f in self.t.sent], [b"\x00" * 4])
        d.stop()

    def test_successor_restores_after_a_crash(self):
        a = self.start("--control")
        a.cycle(0)                                                 # no stop: SIGKILL
        self.t.sent.clear()
        b = self.start("--control")                                # inherits snapshot.json
        b.stop()
        self.assertEqual([f.payload for f in self.t.sent], [b"\x00" * 4])

    def test_without_a_device_the_blob_may_not_control(self):
        class Missing(FakeTransport):
            def can_write(self):
                return False
        self.t = Missing()
        d = self.start("--control")
        self.assertEqual(d.mode, "dry-run")
        self.assertIn("B620798F/121", d.mode_reason)
        self.assertEqual(self.t.sent, [])
        d.cycle(0)
        self.assertEqual(self.target(121), "no Linux path")
        d.stop()

    def test_without_the_kernel_driver_201_has_no_path(self):
        d = self.start("--control", kernel=False)
        self.assertEqual(d.mode, "dry-run")
        self.assertTrue(any(r.channel in (201, 301) for r in d.unwritten))
        d.stop()

    def test_no_ec_disables_the_fan_path(self):
        d = self.start("--control", "--no-ec", transport=None)
        self.assertEqual(d.mode, "dry-run")
        d.stop()

    def test_profile_opt_in_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "x.toml"), "w") as f:
                f.write('[match]\nproduct_name = "X"\n[outputs]\nfan_thresholds = true\n')
            self.assertTrue(supervisor.load_profiles(td)[0].fan_thresholds)
        self.assertFalse(any(p.fan_thresholds for p in supervisor.load_profiles()))     # no shipped profile opts in

    def test_kernel_fan_profiles_option(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.toml")
            with open(path, "w") as f:
                f.write('[match]\nproduct_name = "X"\n')
            self.assertEqual(supervisor.load_profiles(td)[0].kernel_fan_profiles, 1)
            for text, ok in (("2", True), ("3", True), ("0", False), ("4", False), ('"2"', False), ("true", False)):
                with open(path, "w") as f:
                    f.write(f'[match]\nproduct_name = "X"\n[outputs]\nkernel_fan_profiles = {text}\n')
                if ok:
                    self.assertEqual(supervisor.load_profiles(td)[0].kernel_fan_profiles, int(text))
                else:
                    with self.assertRaises(ValueError):
                        supervisor.load_profiles(td)
        ls2 = next(p for p in supervisor.load_profiles() if p.file == "surface-laptop-studio-2.toml")
        self.assertEqual((ls2.product, ls2.variant, ls2.tested, ls2.kernel_fan_profiles),
                         ("Surface Laptop Studio 2", "direct", False, 1))     # 2 only with a kernel that has fan_count
        self.assertEqual({p.file for p in supervisor.load_profiles() if p.kernel_fan_profiles != 1}, set())


class ReportProbeTest(unittest.TestCase):
    def test_probe_reads_only(self):
        t = FakeTransport()
        t.answers = {(5, 0x01, 1): struct.pack("<H", 2300), (5, 0x13, 0): struct.pack("<f", 21.5)}
        lines = report_tool.fan_probe(t)
        self.assertIn("rpm: 2300", lines[0])
        self.assertNotIn("SPL", lines[0])
        self.assertEqual(t.reads, [(5, 0x01, 1)])                 # 0x13 only if a blob output needs it
        self.assertEqual(t.sent, [])
        t.reads.clear()
        lines = report_tool.fan_probe(t, want_spl=True)
        self.assertIn("system SPL: 21.5", lines[0])
        self.assertEqual(t.reads, [(5, 0x01, 1), (5, 0x13, 0)])

    def test_probe_skipped_without_the_device(self):
        t = FakeTransport(root_needed=True)
        self.assertIn("skipped", report_tool.fan_probe(t)[0])
        self.assertEqual(t.reads, [])


# --- scripts/fan-test.py ---

def load_script():
    spec = importlib.util.spec_from_file_location(
        "fan_test", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "fan-test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeEnv:
    """The machine of the test script: a fake clock, scripted temperatures, a recording transport."""

    def __init__(self, mod, log_file=None):
        self.mod, self.t = mod, 0.0
        self.root = "/nonexistent"
        self.transport = FakeTransport()
        self.log_file = log_file
        self.skin, self.pkg, self.rpm_v = 32.0, 50.0, 2000
        self.on_tick = lambda t: None
        self.root_ok, self.ac_v, self.bat, self.services, self.load, self.locked = True, True, 80, [], 0.2, False
        self.profile = "balanced"
        self.lock_path = "/fake/lock"

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.on_tick(self.t)

    def is_root(self):
        return self.root_ok

    def temps(self):
        return self.skin, self.pkg

    def rpm(self):
        return self.rpm_v

    def system_spl(self):
        return None

    def ac(self):
        return self.ac_v

    def battery_pct(self):
        return self.bat

    def services_active(self):
        return self.services

    def platform_profile(self):
        return self.profile

    def loadavg(self):
        return self.load

    def product(self):
        return "Fake"

    def kernel(self):
        return "0"

    def take_lock(self):
        return not self.locked

    def release_lock(self):
        pass

    def transport_factory(self):
        return FileTransport(self.log_file)


class FileTransport:
    """Used by the watchdog process: appends every frame it is asked to send to a file."""

    def __init__(self, path):
        self.path = path

    def send(self, tc, cid, iid, payload, tid=1):
        with open(self.path, "a") as f:
            f.write(Frame(tc, iid, cid, bytes(payload), tid).wire().hex() + "\n")
        return True


class FanScriptTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_script()
        self.out = tempfile.mkdtemp()
        self.logf = os.path.join(self.out, "watchdog.log")
        self.env = FakeEnv(self.mod, self.logf)
        self.mod.BASELINE_S, self.mod.SETTLE_S, self.mod.VERIFY_S, self.mod.HOLD_S = 5, 2, 3, 4
        self.addCleanup(lambda: [signal.signal(s, signal.SIG_DFL) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)])

    def run_script(self, *args, env=True):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                code = self.mod.main([self.out, "--no-blob", *args], self.env if env else None)
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def result(self):
        with open(os.path.join(self.out, "fan.json")) as f:
            return json.load(f)

    def sent(self):
        return [(f.cid, f.payload) for f in self.env.transport.sent]

    def test_dry_run_prints_frames_and_opens_nothing(self):
        class Boom(FakeEnv):
            def take_lock(self):
                raise AssertionError("no lock in a dry run")
        self.env = Boom(self.mod)
        code, out = self.run_script("--channel", "201", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("050101000e010002", out)                     # balanced = 2
        self.assertIn("050101000e010003", out)                     # one step up
        self.assertIn("dry run: nothing sent", out)
        self.assertEqual(self.env.transport.sent, [])

    def test_dry_run_other_channels(self):
        code, out = self.run_script("--channel", "101", "--dry-run")
        self.assertIn("wire 050101000b0200" + struct.pack("<H", 3000).hex(), out)
        self.assertIn("restore FAN 0x0b iid 1         payload 0000", out)

    def test_normal_run_restores_and_logs(self):
        code, _ = self.run_script("--channel", "121")
        self.assertEqual(code, 0)
        self.assertEqual(self.sent(), [(0x12, struct.pack("<f", 20)), (0x12, b"\x00" * 4)])
        r = self.result()
        self.assertEqual(r["restore_errors"], [])
        self.assertEqual(r["verify"]["result"], "fan speed is back near the baseline")
        self.assertTrue({"baseline", "write", "settle", "verify"} <= {s[4] for s in r["log"]})

    def test_profile_run_restores_the_profile_value(self):
        self.env.profile = "balanced-performance"
        code, _ = self.run_script("--channel", "201")
        self.assertEqual(code, 0)
        self.assertEqual(self.sent(), [(0x0E, b"\x03"), (0x0E, b"\x04"), (0x0E, b"\x03")])

    def test_refusals(self):
        cases = [("root_ok", False, "needs root"), ("ac_v", False, "AC"), ("bat", 49, "battery"),
                 ("services", ["surface-powerd.service"], "surface-powerd.service"), ("skin", 40.5, "skin sensor"),
                 ("pkg", 70.0, "CPU package"), ("skin", None, "sensor")]
        for attr, val, text in cases:
            with self.subTest(attr=attr, val=val):
                self.env = FakeEnv(self.mod, self.logf)
                setattr(self.env, attr, val)
                code, _ = self.run_script("--channel", "121")
                self.assertIn("refusing to start", str(code))
                self.assertIn(text, str(code))
                self.assertEqual(self.env.transport.sent, [])

    def test_refuses_with_the_lock_held_and_for_heavy_load(self):
        self.env.locked = True
        code, _ = self.run_script("--channel", "121")
        self.assertIn("lock", str(code))
        self.env = FakeEnv(self.mod, self.logf)
        self.env.load = 2.0
        code, _ = self.run_script("--channel", "111")
        self.assertIn("light load", str(code))

    def test_start_limits_can_only_be_lowered(self):
        code, _ = self.run_script("--channel", "121", "--skin-start", "41")
        self.assertIn("only be lowered", str(code))
        self.env.skin = 36.0
        code, _ = self.run_script("--channel", "121", "--skin-start", "35")
        self.assertIn("skin sensor", str(code))

    def test_blob_ranges_clamp_and_missing_channel_refuses(self):
        plan = self.mod.build_plan(101, None, {101: (0, 2500)})
        self.assertEqual(plan.steps[0].frame.payload, struct.pack("<H", 2500))
        with self.assertRaises(self.mod.Refuse):
            self.mod.build_plan(121, None, {101: (0, 2500)})

    def test_never_301_never_tmp_never_thresholds(self):
        for fr in (Frame(5, 1, 0x0F, b"\x02"), Frame(5, 1, 0x19, b"\x00\x0c"), Frame(3, 1, 0x25, b"\x00\x0c"),
                   Frame(5, 2, 0x0B, b"\x00\x00"), Frame(5, 1, 0x0B, struct.pack("<H", 10001))):
            with self.subTest(fr=fr.label()):
                with self.assertRaises(self.mod.Refuse):
                    self.mod.check_frame(fr)
        self.assertEqual(set(self.mod.WRITABLE), {101, 111, 114, 121, 124, 201})

    def test_hot_skin_aborts_and_restores(self):
        def heat(t):
            if t >= 7:
                self.env.skin = 44.5
        self.env.on_tick = heat
        code, out = self.run_script("--channel", "121")
        self.assertEqual(code, 2)
        self.assertIn("ABORTED: skin sensor reached 44.5", out)
        self.assertEqual(self.sent()[-1], (0x12, b"\x00" * 4))
        self.assertEqual(self.result()["aborted"][:11], "skin sensor")

    def test_package_limit_and_sensor_loss_abort(self):
        for what in ("pkg", "lost"):
            with self.subTest(what=what):
                self.env = FakeEnv(self.mod, self.logf)

                def tick(t, what=what):
                    if t >= 7:
                        if what == "pkg":
                            self.env.pkg = 85.0
                        else:
                            self.env.skin = None
                self.env.on_tick = tick
                code, out = self.run_script("--channel", "124")
                self.assertEqual(code, 2)
                self.assertEqual(self.sent()[-1], (0x14, b"\x00"))

    def test_ten_minute_limit(self):
        self.mod.BASELINE_S, self.mod.RUN_S, self.mod.HOLD_S = 5, 8, 30
        code, out = self.run_script("--channel", "121")
        self.assertEqual(code, 2)
        self.assertIn("minute limit", out)
        self.assertEqual(self.sent()[-1], (0x12, b"\x00" * 4))

    def test_a_write_is_held_at_most_30_s(self):
        fresh = load_script()
        self.assertEqual((fresh.HOLD_S, fresh.RUN_S, fresh.WATCHDOG_S), (30, 600, 60))
        self.assertEqual(fresh.Step("x", fan.neutral_frame(101), 600).hold_s, 30)

    def test_signals_restore(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig):
                self.env = FakeEnv(self.mod, self.logf)

                def kill(t, sig=sig, env=self.env):
                    if t == 8 and not getattr(env, "done", False):
                        env.done = True
                        os.kill(os.getpid(), sig)
                self.env.on_tick = kill
                code, out = self.run_script("--channel", "101")
                self.assertEqual(code, 2)
                self.assertIn(f"signal {int(sig)}", out)
                self.assertEqual(self.sent()[-1], (0x0B, b"\x00\x00"))

    def test_failed_restore_exits_1_and_leaves_it_to_the_watchdog(self):
        def fail_after_write(t):
            if t >= 8:
                self.env.transport.fail = True
        self.env.on_tick = fail_after_write
        code, out = self.run_script("--channel", "121")
        self.assertEqual(code, 1)
        self.assertIn("RESTORE FAILED", out)
        self.assertEqual(len(self.result()["restore_errors"]), 1)

    def test_probe_reads_and_writes_nothing(self):
        self.env.transport.answers = {}
        code, out = self.run_script("--probe", "--seconds", "3")
        self.assertEqual(code, 0)
        self.assertEqual(self.env.transport.sent, [])
        self.assertEqual(len(self.result()["log"]), 3)

    def test_probe_works_without_sensors_and_root(self):
        self.env.root_ok, self.env.pkg, self.env.skin = False, None, None
        code, _ = self.run_script("--probe", "--seconds", "2")
        self.assertEqual(code, 0)

    def test_channel_or_probe_is_required(self):
        code, _ = self.run_script()
        self.assertIn("--channel", str(code))


class WatchdogTest(unittest.TestCase):
    """The watchdog is a forked process; it records its frames in a file."""

    def setUp(self):
        self.mod = load_script()
        self.tmp = tempfile.mkdtemp()
        self.logf = os.path.join(self.tmp, "wd.log")

    def frames(self):
        try:
            with open(self.logf) as f:
                return f.read().split()
        except OSError:
            return []

    def wait_for(self, n, seconds=5.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end and len(self.frames()) < n:
            time.sleep(0.05)
        return self.frames()

    def start(self, timeout=60.0):
        wd = self.mod.Watchdog(lambda: FileTransport(self.logf), timeout_s=timeout)
        wd.start()
        wd.add_restore(fan.neutral_frame(121))
        wd.add_restore(fan.neutral_frame(101))
        return wd

    def test_clean_stop_sends_nothing(self):
        wd = self.start()
        wd.beat()
        wd.stop(clean=True)
        self.assertEqual(self.frames(), [])

    def test_parent_death_sends_the_restore_frames(self):
        wd = self.start()
        os.close(wd.w)                                  # what the kernel does when the parent dies: the pipe closes
        wd.w = None
        got = self.wait_for(2)
        os.waitpid(wd.pid, 0)
        self.assertEqual(sorted(got), sorted([Frame(5, 1, 0x12, b"\0" * 4).wire().hex(), Frame(5, 1, 0x0B, b"\0\0").wire().hex()]))

    def test_unclean_stop_sends_the_restore_frames(self):
        wd = self.start()
        wd.stop(clean=False)
        self.assertEqual(len(self.frames()), 2)

    def test_missing_heartbeat_sends_the_restore_frames(self):
        wd = self.start(timeout=0.5)
        got = self.wait_for(2)
        self.assertEqual(len(got), 2)
        wd.stop(clean=True)

    def test_heartbeat_keeps_it_quiet(self):
        wd = self.start(timeout=1.0)
        for _ in range(6):
            time.sleep(0.3)
            wd.beat()
        self.assertEqual(self.frames(), [])
        wd.stop(clean=True)
        self.assertEqual(self.frames(), [])


if __name__ == "__main__":
    unittest.main()
