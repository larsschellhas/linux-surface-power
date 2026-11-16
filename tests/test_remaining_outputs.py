"""The four remaining Pro 10 outputs (docs/research/12): PL1 time window, sensor offset, and the two "not needed" outputs. Fakes only."""
import io
import os
import struct
import tempfile
import unittest

from fakesys import FakeSys
from helpers import G, BlobBuilder, LIMITS, SYSTEM, TEMP

from surface_power import daemon, monitor
from surface_power.adapters import fan, notneeded, outputs
from surface_power.adapters.fan import FanStage, Frame
from surface_power.adapters.inputs import COMPOSITE, CpuTempAverage
from surface_power.loader.model import ChannelRef

WIN = "sys/class/powercap/intel-rapl:0/constraint_0_time_window_us"
SOC = ChannelRef(outputs.SOC, 61)
OFF9 = ChannelRef(fan.SEN_OFFSET_GUID, 509)
BATT = notneeded.BATT_MODE
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
        self.assertIsNotNone(notneeded.reason(BATT, "/nonexistent"))          # unknown vendor: fake roots
        fs = FakeSys()
        try:
            fs.write("proc/cpuinfo", "vendor_id\t: AuthenticAMD")
            self.assertIsNone(notneeded.reason(BATT, fs.root))
            self.assertIsNotNone(notneeded.reason(MIN2, fs.root))
            fs.write("proc/cpuinfo", "vendor_id\t: GenuineIntel")
            self.assertIsNotNone(notneeded.reason(BATT, fs.root))
            self.assertIsNone(notneeded.reason(OFF9, fs.root))
        finally:
            fs.cleanup()


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
