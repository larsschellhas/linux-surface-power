"""The four remaining Pro 10 outputs (docs/research/12): PL1 time window, sensor offset, battery mode (EC request) and the
"not needed" O_MINPWR2. Fakes only."""
import io
import json
import os
import struct
import tempfile
import unittest

from fakesys import FakeSys
from helpers import G, BlobBuilder, LIMITS, SYSTEM, TEMP

from surface_power import daemon, monitor
from surface_power.adapters import fan, notneeded, outputs, prochot
from surface_power.adapters.fan import FanStage, Frame
from surface_power.adapters.inputs import COMPOSITE, CpuTempAverage
from surface_power.loader.model import ChannelRef

WIN = "sys/class/powercap/intel-rapl:0/constraint_0_time_window_us"
SOC = ChannelRef(outputs.SOC, 61)
OFF9 = ChannelRef(fan.SEN_OFFSET_GUID, 509)
BATT = prochot.BATT_MODE
MIN2 = notneeded.MINPWR2


class TimeWindowTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def test_rounds_up_like_windows(self):
        st = outputs.OutputStage(self.fs.root, {})
        t = st.targets[SOC]
        got = {ms: round(t.shape(ms * 1000) / 1e6, 3) for ms in (500, 1000, 8000, 28000, 30000, 200000)}
        self.assertEqual(got, {500: 1.0, 1000: 1.0, 8000: 8.0, 28000: 28.0, 30000: 32.0, 200000: 112.0})
        # Windows compares the ms value with the unit counts: 1024 ms still gives the 1.0 s step, 1025 ms the next one.
        self.assertEqual((t.shape(1_024_000), t.shape(1_025_000)), (1_000_000, 1_250_000))

    def test_writes_and_snapshot_key(self):
        st = outputs.OutputStage(self.fs.root, {})
        res = st.apply({SOC: 28000})[SOC]
        self.assertEqual((res.kind, res.written), ("written", True))
        self.assertEqual(int(self.fs.read(WIN)), 28000000)
        self.assertIn(self.fs.path(WIN), st.snapshot_keys())
        self.assertEqual(len(st.snapshot_keys()), len(set(st.snapshot_keys())))
        self.assertEqual(st.foreign_changes(), [])
        self.fs.write(WIN, 8000000)
        self.assertEqual(len(st.foreign_changes()), 1)

    def test_coarse_readback_is_no_foreign_change(self):
        st = outputs.OutputStage(self.fs.root, {})
        st.apply({SOC: 30000})
        self.fs.write(WIN, 28000000)                  # the driver may hold only 28 s
        self.assertEqual(st.foreign_changes(), [])
        self.assertFalse(st.apply({SOC: 30000})[SOC].written)          # no rewrite every cycle

    def test_dry_run_and_missing_file(self):
        st = outputs.OutputStage(self.fs.root, {}, dry_run=True)
        self.assertEqual(st.apply({SOC: 28000})[SOC].kind, "dry-run")
        self.assertEqual(int(self.fs.read(WIN)), 27983872)
        os.remove(self.fs.path(WIN))
        st = outputs.OutputStage(self.fs.root, {})
        self.assertNotIn(SOC, st.targets)
        self.assertEqual(st.apply({SOC: 28000})[SOC].kind, "log-only")


class SensorOffsetTest(unittest.TestCase):
    def test_frame_layout(self):
        sp = fan.spec_for(OFF9)
        f = fan.frame_for(sp, 15)
        self.assertEqual((f.tc, f.cid, f.iid, f.flags), (3, 0x24, 9, 0))
        self.assertEqual(f.payload[0], 9)
        self.assertAlmostEqual(struct.unpack("<f", f.payload[1:])[0], 1.5, places=5)
        self.assertEqual(len(f.wire()), 7 + 5)
        n = fan.neutral_frame(sp)
        self.assertEqual(struct.unpack("<f", n.payload[1:])[0], 0.0)

    def test_stage_sends_clamps_and_records_neutral(self):
        sent, rec = [], {}

        class T:
            path = ""

            def can_write(self):
                return True

            def needs_root(self):
                return False

            def send(self, tc, cid, iid, payload, tid=1):
                sent.append(Frame(tc, iid, cid, bytes(payload), tid))
                return True

        fs = FakeSys()
        try:
            st = FanStage(fs.root, [OFF9], {}, transport=T(), on_touch=lambda k, fr: rec.update({k: fr}))
            self.assertTrue(st.has_path(OFF9))
            r = st.apply({OFF9: 400}, 0)[OFF9]               # 40 K: clamped to +5 K
            self.assertEqual((r.kind, r.value), ("written", 50))
            self.assertIn("sam:A79674B4/509", rec)
            self.assertEqual(struct.unpack("<f", rec["sam:A79674B4/509"].payload[1:])[0], 0.0)
            self.assertAlmostEqual(struct.unpack("<f", sent[0].payload[1:])[0], 5.0, places=5)
            dry = FanStage(fs.root, [OFF9], {}, transport=T(), dry_run=True)
            n = len(sent)
            self.assertEqual(dry.apply({OFF9: 0}, 0)[OFF9].kind, "dry-run")
            self.assertEqual(len(sent), n)
        finally:
            fs.cleanup()


class NotNeededTest(unittest.TestCase):
    def test_reasons(self):
        self.assertIsNotNone(notneeded.reason(MIN2, "/nonexistent"))
        self.assertIsNone(notneeded.reason(BATT, "/nonexistent"))             # battery mode has a path now, never "not needed"
        fs = FakeSys()
        try:
            fs.write("proc/cpuinfo", "vendor_id\t: AuthenticAMD")
            self.assertIsNotNone(notneeded.reason(MIN2, fs.root))
            self.assertIsNone(notneeded.reason(BATT, fs.root))
            self.assertIsNone(notneeded.reason(OFF9, fs.root))
        finally:
            fs.cleanup()


class _Rec:
    """A fake SSAM transport: records requests, fails on demand."""
    path = ""

    def __init__(self, ok=True, can=True, root=False):
        self.sent, self.ok, self.can, self.root = [], ok, can, root

    def can_write(self):
        return self.can

    def needs_root(self):
        return self.root

    def send(self, tc, cid, iid, payload, tid=1):
        self.sent.append((tc, tid, iid, cid, bytes(payload)))
        return self.ok

    def read(self, *a, **k):
        return None


class BatteryModeTest(unittest.TestCase):
    """0B7B5350/70: one SAM TMP 0x1b request when the value turns 1; nothing for 0, nothing to restore (docs/research/12 2.2)."""

    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def stage(self, t, **kw):
        return prochot.ProchotStage(self.fs.root, [BATT], transport=t, **kw)

    def test_frame_layout(self):
        self.assertEqual(prochot.FRAME.wire(), bytes([0x03, 0x01, 0x00, 0x00, 0x1B, 0x00, 0x00]))   # TMP, tid 1, iid 0, flags 0, cid 0x1b

    def test_sent_once_per_episode(self):
        t = _Rec()
        st = self.stage(t)
        self.assertTrue(st.has_path(BATT))
        self.assertEqual(st.apply({BATT: 0}, 0)[BATT].kind, "idle")
        self.assertEqual(t.sent, [])                                          # value 0: nothing (Windows sends no "off")
        r = st.apply({BATT: 1}, 1000)[BATT]
        self.assertEqual((r.kind, r.written), ("written", True))
        self.assertEqual(t.sent, [(0x03, 1, 0, 0x1B, b"")])
        self.assertFalse(st.apply({BATT: 1}, 2000)[BATT].written)             # same episode: not again
        self.assertFalse(st.apply({BATT: 1}, 60000)[BATT].written)
        st.apply({BATT: 0}, 61000)
        self.assertTrue(st.apply({BATT: 1}, 62000)[BATT].written)             # a new episode
        self.assertEqual(len(t.sent), 2)
        st.forget()                                                           # resume: sent again
        self.assertTrue(st.apply({BATT: 1}, 63000)[BATT].written)
        self.assertEqual(len(t.sent), 3)

    def test_only_one_counts(self):
        t = _Rec()
        st = self.stage(t)
        for v in (0, 2, 3, 7):
            st.apply({BATT: v}, 0)
        self.assertEqual(t.sent, [])

    def test_dry_run_sends_nothing(self):
        t = _Rec()
        st = self.stage(t, dry_run=True)
        r = st.apply({BATT: 1}, 0)[BATT]
        self.assertEqual((r.kind, r.written), ("dry-run", False))
        self.assertEqual(t.sent, [])
        self.assertEqual(st.status()[0]["would"], "03010000" "1b0000")

    def test_failures_retry_then_log_only(self):
        t = _Rec(ok=False)
        st = self.stage(t)
        self.assertEqual(st.apply({BATT: 1}, 0)[BATT].kind, "pending")
        self.assertEqual(st.apply({BATT: 1}, 1000)[BATT].kind, "pending")     # rate limit: no new request
        self.assertEqual(len(t.sent), 1)
        st.apply({BATT: 1}, prochot.RATE_MS)
        r = st.apply({BATT: 1}, 2 * prochot.RATE_MS)[BATT]
        self.assertEqual(r.kind, "log-only")
        self.assertFalse(st.has_path(BATT))
        self.assertTrue(any(p == "warning" for p, _ in st.drain_events()))

    def test_sam_machine_without_usable_device_says_so(self):
        """Pro 10 without root or cdev: the machine has SAM, the debug device is only not usable now."""
        self.fs.write("sys/bus/surface_aggregator/devices/01:0b:01:00:01/modalias", "x")
        st = self.stage(_Rec(can=False))
        self.assertFalse(st.has_path(BATT, report=True))
        self.assertTrue(st.needs_cdev(BATT))
        self.assertTrue(any("needs root and surface_aggregator_cdev" in n for n in st.notes))
        self.assertFalse(any("DPHT" in n for n in st.notes))
        self.assertIn("surface_aggregator_cdev", st.describe(BATT))
        self.assertEqual(st.apply({BATT: 1}, 0)[BATT].kind, "log-only")

    def test_acpi_ec_machine_keeps_the_dpht_text(self):
        st = self.stage(_Rec(can=False))
        self.assertFalse(st.needs_cdev(BATT))
        self.assertTrue(any("DPHT" in n for n in st.notes))

    def test_no_path(self):
        self.assertFalse(self.stage(_Rec(can=False)).has_path(BATT))          # no debug device (ACPI-EC machines)
        st = self.stage(_Rec(can=False, root=True))
        self.assertFalse(st.has_path(BATT))
        self.assertTrue(st.has_path(BATT, report=True))                       # the report counts "needs root" as a path
        self.fs.write("proc/cpuinfo", "vendor_id\t: AuthenticAMD")
        self.assertFalse(self.stage(_Rec()).has_path(BATT))                   # only the Intel client is analysed
        self.assertEqual(self.stage(_Rec()).describe(BATT), "no Linux path")
        self.assertFalse(prochot.ProchotStage(self.fs.root, [], transport=_Rec()).refs)   # blob without the output


class CpuTempTest(unittest.TestCase):
    def test_source(self):
        fs = FakeSys()
        try:
            s = CpuTempAverage(fs.root)
            self.assertEqual(s.read(0)[ChannelRef(COMPOSITE, 112)], 3231)
        finally:
            fs.cleanup()


class DaemonReadinessTest(unittest.TestCase):
    """A blob with the four outputs plus PL1: control is allowed, the status says what is written and what is not needed."""

    def blob(self):
        b = BlobBuilder()
        b.input_channel(TEMP, 509, 3681, 2731)
        b.output_channel(LIMITS, 51, 25000, 31000, 7500)
        b.line(G["pass_through"], 1, [(SYSTEM, 1)], [(LIMITS, 51)], [25000])
        b.line(G["pass_through"], 2, [(SYSTEM, 1)], [(LIMITS, 70)], [0])
        b.line(G["pass_through"], 3, [(SYSTEM, 1)], [(outputs.SOC, 61)], [28000])
        b.line(G["pass_through"], 4, [(SYSTEM, 1)], [(fan.SEN_OFFSET_GUID, 509)], [0])
        b.line(G["pass_through"], 5, [(SYSTEM, 1)], [(notneeded.MINPWR2.guid, 3)], [0])
        return b.text()

    def test_status(self):
        fs, run, state = FakeSys(), tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        try:
            path = os.path.join(run.name, "blob.txt")
            with open(path, "w") as f:
                f.write(self.blob())
            args = daemon.parse_args(["--root", fs.root, "--blob", path, "--run-dir", run.name, "--state-dir", state.name,
                                      "--no-ec", "--control"])
            d = daemon.Daemon(args, monitor.Journal(stream=io.StringIO()), fan_transport=_Ok())
            self.assertEqual(d.unwritten, [])
            self.assertEqual(d.mode, "control")
            d.cycle(0)
            with open(os.path.join(run.name, "status.json")) as f:
                status = json.load(f)
            targets = {o["channel"]: o["target"] for o in status["outputs"]}
            self.assertEqual(targets["0B7B5350/70"], "SAM TMP 0x1b (cdev)")
            self.assertTrue(targets["110DF2A4/3"].startswith("not needed on Linux"))
            self.assertEqual(status["battery_mode"][0]["path"], "SAM TMP 0x1b (cdev)")
            d.stop()
        finally:
            fs.cleanup()
            run.cleanup()
            state.cleanup()

    def test_value_one_sends_the_request_in_control_mode(self):
        """End to end: the blob turns the slider state (5 on AC with the fake profile) into battery mode 1; one request goes out."""
        fs, run, state = FakeSys(), tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        try:
            b = BlobBuilder()
            b.input_channel(TEMP, 509, 3681, 2731)
            b.output_channel(LIMITS, 51, 25000, 31000, 7500)
            b.line(G["pass_through"], 1, [(SYSTEM, 1)], [(LIMITS, 51)], [25000])
            b.line(G["lookup_table"], 2, [(SYSTEM, 1)], [(LIMITS, 70)], [1, 0, 0, 5, 5, 1])
            path = os.path.join(run.name, "blob.txt")
            with open(path, "w") as f:
                f.write(b.text())
            args = daemon.parse_args(["--root", fs.root, "--blob", path, "--run-dir", run.name, "--state-dir", state.name,
                                      "--no-ec", "--control"])
            t = _Rec()
            d = daemon.Daemon(args, monitor.Journal(stream=io.StringIO()), fan_transport=t)
            self.assertEqual(d.mode, "control")
            d.cycle(0)
            d.cycle(1000)
            self.assertEqual([x for x in t.sent if x[3] == 0x1B], [(0x03, 1, 0, 0x1B, b"")])
            d.after_resume(2000)
            d.cycle(3000)
            self.assertEqual(len([x for x in t.sent if x[3] == 0x1B]), 2)       # sent again after a resume
            d.stop()
            self.assertEqual([x for x in t.sent if x[3] != 0x1B and x[3] != 0x24], [])   # no restore frame for battery mode
        finally:
            fs.cleanup()
            run.cleanup()
            state.cleanup()

    def test_battery_mode_without_path_blocks_control(self):
        """No SSAM debug device: the output has no Linux path, so the blob may not control (spec supervisor 2.6)."""
        fs, run, state = FakeSys(), tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        try:
            path = os.path.join(run.name, "blob.txt")
            with open(path, "w") as f:
                f.write(self.blob())
            args = daemon.parse_args(["--root", fs.root, "--blob", path, "--run-dir", run.name, "--state-dir", state.name,
                                      "--no-ec", "--control"])
            d = daemon.Daemon(args, monitor.Journal(stream=io.StringIO()), fan_transport=_Rec(can=False))
            self.assertIn("0B7B5350/70", [str(r) for r in d.unwritten])
            self.assertEqual(d.mode, "dry-run")
            d.stop()
        finally:
            fs.cleanup()
            run.cleanup()
            state.cleanup()


class _Ok:
    path = ""

    def can_write(self):
        return True

    def needs_root(self):
        return False

    def send(self, *a, **k):
        return True

    def read(self, *a, **k):
        return None


if __name__ == "__main__":
    unittest.main()
