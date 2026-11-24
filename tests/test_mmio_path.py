"""Daemon side of the MMIO write path (spec kernel-mmio-daemon.md, plan B12): the fake sysfs, path choice, writing, snapshot and
restore, foreign changes, resume. Test IDs MD-n and MB-n are those of the spec's section 12."""
import io
import json
import os
import tempfile
import unittest

from fakesys import MCHBAR, MMIO_PARAM, MMIO_ZONE, PROC_THERMAL, FakeSys
from helpers import LIMITS as HL, SYSTEM, TEMP, BlobBuilder, G
from test_daemon import blob_text

from unittest import mock

from surface_power import daemon, monitor, supervisor
from surface_power.adapters import outputs, rapl_copies
from surface_power.adapters.outputs import LIMITS, SOC, WINDOW_CHANNEL, OutputStage, PathFailure
from surface_power.loader.model import ChannelRef, OutputChannel

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



PL1, PL2, PL4, PSYS1 = (ChannelRef(LIMITS, c) for c in (51, 52, 53, 54))
WINDOW = ChannelRef(SOC, WINDOW_CHANNEL)


def sb3_profile(**kw):
    """The SB3 profile's options (pl3, pl4 on), tested, plus `kw`."""
    return supervisor.Profile("x.toml", None, None, "classic", True, pl3=True, pl4=True, **kw)


class PathChoiceTest(MmioDaemonBase):
    """Step 3: spec 2 (choice), 9 (status and log), 11 (dry-run). MD-1 ... MD-6, MD-23."""

    def zone_of(self, d, ref):
        return os.path.dirname(d.outputs.targets[ref].path)

    def test_md1_writable_module_zone_gives_the_mmio_path(self):
        self.fs.add_mmio_zone()
        self.fs.add_msr()                                                        # time unit 10 from MSR 0x606 (4.3)
        d = self.start("--control")
        self.assertEqual(d.pl_path.kind, "mmio")
        mmio, msrz = self.fs.path(MMIO_ZONE), self.fs.path(MSR_ZONE)
        self.assertEqual(self.zone_of(d, PL1), mmio)
        self.assertEqual(self.zone_of(d, PL2), mmio)
        self.assertEqual(self.zone_of(d, WINDOW), mmio)
        self.assertEqual(self.zone_of(d, PL4), msrz)                              # PL4 and psys stay on the MSR zones (2.3)
        self.assertEqual(self.zone_of(d, PSYS1), self.fs.path("sys/class/powercap/intel-rapl:1"))
        self.assertEqual(self.out.getvalue().count("PL path mmio"), 1)
        d.cycle(0)
        st = self.status()
        self.assertEqual(st["pl_path"], "mmio")
        self.assertIn("surface_rapl_mmio", st["pl_path_reason"])
        d.stop()

    def test_md2_read_only_module_zone_gives_msr(self):
        self.fs.add_mmio_zone(allow_write="N")
        d = self.start("--control")
        self.assertEqual(d.pl_path.kind, "msr")
        self.assertIn("read-only", d.pl_path.reason)
        self.assertEqual(self.zone_of(d, PL1), self.fs.path(MSR_ZONE))
        d.stop()

    def test_md3_no_zone_gives_msr(self):
        d = self.start("--control")
        self.assertEqual((d.pl_path.kind, d.pl_path.reason), ("msr", "no MMIO zone"))
        self.assertEqual(self.zone_of(d, PL1), self.fs.path(MSR_ZONE))
        d.cycle(0)
        self.assertEqual(self.status()["pl_path"], "msr")
        d.stop()

    def test_md4_kernel_zone_counts_as_writable_and_its_pl4_and_dram_stay_untouched(self):
        self.fs.add_mmio_zone("kernel")
        before_pl4 = self.fs.read(f"{MMIO_ZONE}/constraint_2_power_limit_uw")
        before_dram = self.fs.read(f"{MMIO_ZONE}:0/constraint_0_power_limit_uw")
        with mock.patch.object(supervisor, "find_profile", return_value=sb3_profile()):
            d = self.start("--control")
        self.assertEqual(d.pl_path.kind, "mmio")
        self.assertIn("processor thermal", d.pl_path.reason)
        self.assertEqual(self.zone_of(d, PL4), self.fs.path(MSR_ZONE))
        for t in range(3):
            d.cycle(t * 1000)
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_2_power_limit_uw"), before_pl4)
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}:0/constraint_0_power_limit_uw"), before_dram)
        d.stop()

    def test_md5_marker_or_profile_switch_gives_msr(self):
        self.fs.add_mmio_zone()
        with open(os.path.join(self.run.name, "mmio-unusable"), "w") as f:
            f.write("constraint write failed\n")
        d = self.start("--control")
        self.assertEqual(d.pl_path.kind, "msr")
        self.assertIn("mmio-unusable", d.pl_path.reason)
        d.stop()
        os.remove(os.path.join(self.run.name, "mmio-unusable"))
        with mock.patch.object(supervisor, "find_profile", return_value=sb3_profile(mmio=False)):
            d = self.start("--control")
        self.assertEqual(d.pl_path.kind, "msr")
        self.assertIn("outputs.mmio = false", d.pl_path.reason)
        d.stop()

    def test_md5_profile_option_is_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.toml"), "w") as f:
                f.write('[match]\nproduct_name = "X"\n[outputs]\nmmio = false\n')
            with open(os.path.join(tmp, "b.toml"), "w") as f:
                f.write('[match]\nproduct_name = "Y"\n')
            a, b = supervisor.load_profiles(tmp)
            self.assertIs(a.mmio, False)
            self.assertIsNone(b.mmio)
            with open(os.path.join(tmp, "c.toml"), "w") as f:
                f.write('[match]\nproduct_name = "Z"\n[outputs]\nmmio = "no"\n')
            with self.assertRaises(ValueError):
                supervisor.load_profiles(tmp)

    def test_md6_no_pl1_pl2_or_window_write_to_the_msr_zone(self):
        self.fs.add_mmio_zone()
        self.write_blob(extra=[])
        keys = ("constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_0_time_window_us")
        before = {k: self.fs.read(f"{MSR_ZONE}/{k}") for k in keys}
        d = self.start("--control")
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        self.fs.write("sys/class/power_supply/ADP1/online", 0)       # balanced on battery: state 2 -> 13/15 W
        for t in range(3):
            d.cycle(t * 1000)
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_power_limit_uw"), "13000000")
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_1_power_limit_uw"), "15000000")
        self.assertEqual({k: self.fs.read(f"{MSR_ZONE}/{k}") for k in keys}, before)
        paths = [t.path for t in d.outputs.targets.values() if t.path]
        self.assertEqual(len(paths), len(set(paths)))                 # never two targets on one file
        d.stop()

    def test_md23_dry_run_on_mmio_writes_nothing_and_shows_the_path(self):
        self.fs.add_mmio_zone()
        before, msr_before = self.zone_files(), self.fs.read(f"{MSR_ZONE}/constraint_0_power_limit_uw")
        d = self.start("--dry-run")
        self.fs.write("sys/firmware/acpi/platform_profile", "balanced")
        self.fs.write("sys/class/power_supply/ADP1/online", 0)
        for t in range(3):
            d.cycle(t * 1000)
        self.assertEqual(self.zone_files(), before)
        self.assertEqual(self.fs.read(f"{MSR_ZONE}/constraint_0_power_limit_uw"), msr_before)
        st = self.status()
        self.assertEqual(st["pl_path"], "mmio")
        pl1 = next(o for o in st["outputs"] if o["name"] == "PL1")
        self.assertEqual((pl1["value_w"], pl1["target"]), (13.0, "dry-run"))
        self.assertFalse(os.path.exists(os.path.join(self.run.name, "snapshot.json")))
        d.stop()

    def test_zone_that_appears_later_is_logged_once(self):
        """Spec 2.5: noticed by the 60-second check, the path is not switched at run time."""
        d = self.start("--control")
        d.cycle(0)
        self.fs.add_mmio_zone()
        d.cycle(61000)
        d.cycle(122000)
        self.assertEqual(self.out.getvalue().count("restart the daemon to use it"), 1)
        self.assertEqual(d.pl_path.kind, "msr")
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_power_limit_uw"), "25000000")
        d.stop()



def window_blob(window_ms=28000):
    """PL1 from a slider table (as blob_text), PL2 and the PL1 window (CF2F6076/61) as constants."""
    b = BlobBuilder()
    b.input_channel(TEMP, 509, 3681, 2731)
    b.output_channel(HL, 51, 25000, 31000, 7500)
    b.line(G["lookup_table"], 1, [(SYSTEM, 1)], [(HL, 51)], [6, 25000, 25000, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6,
                                                            25000, 13000, 25000, 25000, 30000, 25000])
    for id_, out, v in ((2, (HL, 52), 20000), (3, (SOC, 61), window_ms)):
        b.line(G["lookup_table"], id_, [(SYSTEM, 1)], [out], [6, v, v, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6] + [v] * 6)
    return b.text()


class Recorder:
    """Records every write of the output stage (constraint files and `enabled`) in order, then writes."""

    def __init__(self, root):
        self.root, self.writes, self.real = root, [], outputs._write

    def __call__(self, path, text):
        self.writes.append((os.path.relpath(path, self.root), text))
        self.real(path, text)

    def names(self, zone=MMIO_ZONE):
        return [(os.path.basename(p), t) for p, t in self.writes if os.path.dirname(p) == zone]


class WritingTest(MmioDaemonBase):
    """Step 4: spec 3 (MSR copy), 4 (first write, enable, values), 6 (bounds). MD-7 ... MD-12, MD-18."""

    def setUp(self):
        super().setUp()
        with open(self.blob, "w") as f:
            f.write(window_blob())
        self.fs.add_msr()

    def control(self, profile=None):
        with mock.patch.object(supervisor, "find_profile", return_value=profile or sb3_profile()):
            return self.start("--control")

    def test_md8_first_write_order_and_enable(self):
        self.fs.add_mmio_zone(enabled=0)
        d = self.control()
        rec = Recorder(self.fs.root)
        with mock.patch.object(outputs, "_write", rec):
            d.cycle(0)
            d.cycle(1000)                                                   # nothing changed: nothing written again
        self.assertEqual(rec.names(), [("constraint_0_time_window_us", "28000000"), ("constraint_0_power_limit_uw", "30000000"),
                                       ("constraint_1_power_limit_uw", "20000000"), ("enabled", "1")])
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/enabled"), "1")
        self.assertEqual(self.out.getvalue().count("enabled=1 written"), 1)
        d.stop()

    def test_md9_enabled_firmware_copy_is_never_enabled_again(self):
        self.fs.add_mmio_zone(enabled=1, pl1_uw=28_000_000, pl2_uw=35_000_000)   # as on the Pro 10 [M]
        d = self.control()
        rec = Recorder(self.fs.root)
        with mock.patch.object(outputs, "_write", rec):
            for t in range(3):
                d.cycle(t * 1000)
        self.assertNotIn("enabled", [n for n, _ in rec.names()])
        self.assertIn(("constraint_0_power_limit_uw", "30000000"), rec.names())
        d.stop()

    def test_md10_enable_that_does_not_stick(self):
        self.fs.add_mmio_zone(enabled=0)
        d = self.control()
        real = outputs._write

        def refuse_enable(path, text):
            real(path, "0" if path.endswith("/enabled") else text)        # the core reports success, the bit stays 0
        with mock.patch.object(outputs, "_write", refuse_enable):
            with self.assertRaises(PathFailure) as cm:
                d.cycle(0)
        self.assertIn("enabled reads 0", str(cm.exception))
        d.stop()
        marker = os.path.join(self.run.name, "mmio-unusable")
        self.assertTrue(os.path.exists(marker))
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_power_limit_uw"), "25000000")   # snapshot restored
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_time_window_us"), "27983872")
        self.assertIn("MMIO path failed", self.out.getvalue())
        d2 = self.control()                                                  # the restart chooses msr for this boot
        self.assertEqual(d2.pl_path.kind, "msr")
        d2.stop()

    def test_md10_constraint_write_fails_with_enodata(self):
        self.fs.add_mmio_zone(enabled=0)
        d = self.control()
        real = outputs._write

        def refuse_pl1(path, text):
            if path.endswith(f"{MMIO_ZONE}/constraint_0_power_limit_uw"):
                raise OSError(61, "No data available")                     # how the module's refusal reaches user space
            real(path, text)
        with mock.patch.object(outputs, "_write", refuse_pl1):
            with self.assertRaises(PathFailure):
                d.cycle(0)
        d.stop()
        self.assertTrue(os.path.exists(os.path.join(self.run.name, "mmio-unusable")))
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/enabled"), "0")
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_time_window_us"), "27983872")

    def test_md10_run_exits_with_an_error(self):
        class Failing:
            def __init__(self, *a, **k):
                self.stopped = False

            def cycle(self, now):
                raise PathFailure("PL1: write failed", marker=True)

            def stop(self):
                Failing.stopped = True
        with mock.patch.object(daemon, "Daemon", Failing):
            self.assertEqual(daemon.run(["--root", self.fs.root, "--run-dir", self.run.name]), 1)
        self.assertTrue(Failing.stopped)

    def test_md7_msr_copy_back_at_its_snapshot_value_once(self):
        self.fs.add_mmio_zone(enabled=0)
        d = self.control()
        # a predecessor on the msr path that crashed: the MSR copy holds its 13 W, the snapshot the firmware's 30 W
        d.snapshot.values[self.fs.path(f"{MSR_ZONE}/constraint_0_power_limit_uw")] = "30000000"
        self.fs.write(f"{MSR_ZONE}/constraint_0_power_limit_uw", 13000000)
        rec = Recorder(self.fs.root)
        with mock.patch.object(outputs, "_write", rec):
            d.cycle(0)
            d.cycle(1000)
        msr_writes = rec.names(MSR_ZONE)
        self.assertEqual(msr_writes, [("constraint_0_power_limit_uw", "30000000")])
        first_mmio = next(i for i, (p, _) in enumerate(rec.writes) if p.startswith(MMIO_ZONE))
        self.assertLess(rec.writes.index((f"{MSR_ZONE}/constraint_0_power_limit_uw", "30000000")), first_mmio)
        self.assertEqual(self.out.getvalue().count("MSR copy set back"), 1)
        d.stop()

    def test_md7_snapshot_holds_the_msr_keys_on_the_mmio_path(self):
        self.fs.add_mmio_zone(enabled=0)
        d = self.control()
        for k in ("constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_0_time_window_us"):
            self.assertIn(self.fs.path(f"{MSR_ZONE}/{k}"), d.snapshot.values)
        d.stop()

    def test_md12_time_unit_not_10_makes_the_window_log_only(self):
        self.fs.add_mmio_zone(enabled=0)
        self.fs.add_mmio_register(raw(25, 61, clamp1=True), units=0x000B0E03)      # register readable: time unit 11
        d = self.control()
        self.assertNotIn(WINDOW, d.outputs.targets)
        self.assertEqual(self.out.getvalue().count("time unit 11"), 1)
        d.stop()

    def test_md12_time_unit_from_msr_0x606_without_the_register(self):
        self.fs.add_mmio_zone(enabled=0)
        self.fs.add_msr({0x606: 0x000B0E03})
        d = self.control()
        self.assertNotIn(WINDOW, d.outputs.targets)
        d.stop()
        self.fs.add_msr({0x606: 0x000A0E03})
        d = self.control()
        self.assertIn(WINDOW, d.outputs.targets)
        d.stop()


class StageTest(unittest.TestCase):
    """The output stage on the mmio path alone: windows (MD-11) and bounds (MD-18)."""

    def setUp(self):
        self.fs = FakeSys()
        self.fs.add_msr()
        self.zone = self.fs.path(self.fs.add_mmio_zone())

    def tearDown(self):
        self.fs.cleanup()

    def stage(self, ranges=None):
        return OutputStage(self.fs.root, ranges or {}, mmio_zone=self.zone)

    def test_md11_windows(self):
        st = self.stage()
        for ms, us in ((1000, 1_000_000), (8000, 8_000_000), (28000, 28_000_000), (30000, 32_000_000)):
            st.apply({WINDOW: ms})
            self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_time_window_us"), str(us))
            self.fs.write(f"{MMIO_ZONE}/constraint_0_time_window_us", int(us * 0.9994))     # what the core reads back [C]
            self.assertEqual(st.foreign_changes(), [])

    def test_adjacent_window_step_is_written_on_either_path(self):
        """28 s -> 32 s is 14 %, inside the read-back tolerance (15 %): before 24.11.2026 the change was not written."""
        for st in (self.stage(), OutputStage(self.fs.root, {})):
            path = os.path.join(os.path.dirname(st.targets[WINDOW].path), "constraint_0_time_window_us")
            st.apply({WINDOW: 28000})
            st.apply({WINDOW: 30000})
            with open(path) as f:
                self.assertEqual(f.read(), "32000000")
            self.assertFalse(st.apply({WINDOW: 30000})[WINDOW].written)

    def test_md18_bounds(self):
        wide = OutputChannel(1, PL1, 25000, 10_000_000, 0)                      # a blob range far beyond the hard limits
        st = self.stage({PL1: wide, PL2: OutputChannel(2, PL2, 25000, 10_000_000, 0)})
        st.apply({PL1: 5_000_000, PL2: 5_000_000})                             # 5000 W
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_power_limit_uw"), "120000000")
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_1_power_limit_uw"), "200000000")
        st.apply({PL1: 1000, PL2: 1000})                                       # 1 W
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_0_power_limit_uw"), "3000000")
        self.assertEqual(self.fs.read(f"{MMIO_ZONE}/constraint_1_power_limit_uw"), "3000000")
        st.apply({PL1: 6_000_000})
        cuts = [e for _p, e in st.drain_events() if "hard limit" in e]
        self.assertEqual(len(cuts), 2, cuts)                                    # one line per target, not per write
        self.assertIn("PL1", cuts[0])


if __name__ == "__main__":
    unittest.main()
