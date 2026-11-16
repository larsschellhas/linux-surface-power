"""Throttle requests 9BBB8910 -> cpufreq and GPU frequency caps (spec outputs 8), on fake sysfs trees."""
import io
import json
import os
import tempfile
import unittest

from fakesys import FakeSys
from helpers import LIMITS, TEMP, ref

from surface_power import daemon, monitor, supervisor
from surface_power.adapters import cpufreq
from surface_power.adapters.cpufreq import THROTTLE, ThrottleStage
from test_daemon import blob_text

P0 = "sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq"
P4 = "sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq"
PARK, CPU1, CLUSTER1, GFX, NPU = (ref(THROTTLE, c) for c in (62, 7, 21, 5, 200))


class ThrottleStageTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.fs.add_cpufreq()

    def tearDown(self):
        self.fs.cleanup()

    def stage(self, channels=(PARK,), **kw):
        return ThrottleStage(self.fs.root, channels, **kw)

    def test_park_caps_all_policies_by_percent_of_the_maximum(self):
        st = self.stage()
        res = st.apply({PARK: 50})
        self.assertEqual((self.fs.read(P0), self.fs.read(P4)), ("2000000", "2000000"))
        self.assertEqual((res[PARK].kind, res[PARK].written), ("written", True))
        self.assertEqual(st.describe(PARK), "cpufreq cap")

    def test_requests_on_one_target_combine_by_minimum(self):
        st = self.stage((PARK, CPU1, CLUSTER1))
        st.apply({PARK: 80, CPU1: 60, CLUSTER1: 70})
        # CPU1 is in policy0, cluster 1 is policy4; the park request reaches both.
        self.assertEqual((self.fs.read(P0), self.fs.read(P4)), ("2400000", "2800000"))

    def test_cap_is_clamped_to_floor_and_range(self):
        st = self.stage()
        st.apply({PARK: 0})
        self.assertEqual(self.fs.read(P0), "800000")                # 20 % of 4 GHz, above cpuinfo_min
        st.apply({PARK: 250})
        self.assertEqual(self.fs.read(P0), "4000000")
        self.fs.write("sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq", 1500000)
        st.apply({PARK: 10})
        self.assertEqual(self.fs.read(P0), "1500000")               # never below the user's minimum

    def test_small_changes_are_not_written(self):
        st = self.stage()
        st.apply({PARK: 50})
        res = st.apply({PARK: 49})                                  # 1 % < 2 % step
        self.assertFalse(res[PARK].written)
        self.assertEqual(self.fs.read(P0), "2000000")
        self.assertTrue(st.apply({PARK: 40})[PARK].written)
        self.assertFalse(st.apply({PARK: 40})[PARK].written)

    def test_100_percent_returns_to_the_value_before_the_first_write(self):
        self.fs.write(P0, 3000000)                                  # the user's own limit
        st = self.stage()
        st.apply({PARK: 90})                                        # 3.6 GHz would be above it
        self.assertEqual(self.fs.read(P0), "3000000")
        st.apply({PARK: 50})
        self.assertEqual(self.fs.read(P0), "2000000")
        st.apply({PARK: 100})
        self.assertEqual(self.fs.read(P0), "3000000")

    def test_baseline_from_a_snapshot_beats_a_leftover_cap(self):
        self.fs.write(P0, 2000000)                                  # left by a crashed predecessor
        st = self.stage()
        st.set_baseline({self.fs.path(P0): "4000000"})
        st.apply({PARK: 100})
        self.assertEqual(self.fs.read(P0), "4000000")

    def test_dry_run_writes_nothing(self):
        st = self.stage(dry_run=True)
        res = st.apply({PARK: 50})
        self.assertEqual(res[PARK].kind, "dry-run")
        self.assertEqual(self.fs.read(P0), "4000000")
        self.assertEqual(st.status()[0]["would"], 2000000)

    def test_cpu_and_cluster_roles(self):
        st = self.stage((CPU1, ref(THROTTLE, 21), ref(THROTTLE, 22)))
        self.assertEqual([t.name for t in st.map[CPU1]], ["cpufreq policy0"])     # channel 7 = CLUS.CPU1
        self.assertEqual([t.name for t in self.stage((ref(THROTTLE, 2),)).map[ref(THROTTLE, 2)]], ["cpufreq policy4"])   # 2 = CPU5
        self.assertEqual([t.name for t in st.map[ref(THROTTLE, 21)]], ["cpufreq policy4"])
        self.assertEqual(st.kind(ref(THROTTLE, 22)), "log-only")    # only two policies here

    def test_unknown_device_and_missing_targets_are_log_only(self):
        st = self.stage((NPU, GFX))
        self.assertEqual((st.kind(NPU), st.kind(GFX)), ("log-only", "log-only"))
        self.assertEqual(st.describe(NPU), "no Linux path")
        self.assertTrue(any("log-only" in n for n in st.notes))
        res = st.apply({NPU: 50, GFX: 50})
        self.assertEqual(res[NPU].kind, "log-only")
        empty = ThrottleStage(tempfile.mkdtemp(), (PARK,))
        self.assertFalse(empty.has_target(PARK))

    def test_failed_write_turns_the_target_log_only(self):
        st = self.stage()
        os.chmod(self.fs.path(P0), 0o400)
        if os.geteuid() == 0:
            self.skipTest("root ignores file modes")
        res = st.apply({PARK: 50})
        self.assertEqual(self.fs.read(P4), "2000000")               # the other policy still works
        self.assertTrue(any("write failed" in n for n in st.notes))
        self.assertEqual(res[PARK].targets, ("cpufreq policy4",))

    def test_foreign_change_is_detected(self):
        st = self.stage()
        st.apply({PARK: 50})
        self.assertEqual(st.foreign_changes(), [])
        self.fs.write(P0, 3000000)
        found = st.foreign_changes()
        self.assertEqual([(f.target, f.expected, f.found) for f in found], [("cpufreq policy0", 2000000, 3000000)])
        st.apply({PARK: 50})                                        # we take it back
        self.assertEqual(self.fs.read(P0), "2000000")

    def test_snapshot_restores_the_values(self):
        st = self.stage()
        snap, _ = supervisor.Snapshot.take(tempfile.mkdtemp(), st.snapshot_keys())
        st.apply({PARK: 50})
        self.assertEqual(self.fs.read(P0), "2000000")
        self.assertEqual(snap.restore(), [])
        self.assertEqual((self.fs.read(P0), self.fs.read(P4)), ("4000000", "4000000"))


class GpuTargetTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()

    def tearDown(self):
        self.fs.cleanup()

    def test_i915(self):
        self.fs.add_i915()
        st = ThrottleStage(self.fs.root, (GFX,))
        self.assertEqual(st.describe(GFX), "GPU freq cap")
        st.apply({GFX: 50})
        self.assertEqual(self.fs.read("sys/class/drm/card0/gt_max_freq_mhz"), "550")
        st.apply({GFX: 5})
        self.assertEqual(self.fs.read("sys/class/drm/card0/gt_max_freq_mhz"), "300")     # not below RPn (floor is 220)
        st.apply({GFX: 100})
        self.assertEqual(self.fs.read("sys/class/drm/card0/gt_max_freq_mhz"), "1100")

    def test_xe(self):
        d = "sys/class/drm/card1/device/tile0/gt0/freq0"
        for k, v in (("rp0_freq", 1800), ("rpn_freq", 400), ("max_freq", 1800), ("min_freq", 400)):
            self.fs.write(f"{d}/{k}", v)
        st = ThrottleStage(self.fs.root, (GFX,))
        st.apply({GFX: 50})
        self.assertEqual(self.fs.read(f"{d}/max_freq"), "900")

    def test_devfreq_gpu(self):
        self.fs.add_devfreq_gpu()
        st = ThrottleStage(self.fs.root, (GFX,))
        st.apply({GFX: 50})
        self.assertEqual(self.fs.read("sys/class/devfreq/3d00000.gpu/max_freq"), "400000000")

    def test_no_gpu_interface_is_log_only(self):
        st = ThrottleStage(self.fs.root, (GFX,))
        self.assertEqual(st.kind(GFX), "log-only")


class DaemonThrottleTest(unittest.TestCase):
    PARK_LINE = ["1,0,0,026CCFCC-A52A-4DB1-8B49-D2BAAFCAD329,40,1,1,99999999-9999-9999-9999-999999999999,1,"
                 f"{THROTTLE},62,50"]

    def setUp(self):
        self.fs = FakeSys()
        self.fs.add_cpufreq()
        self.run = tempfile.TemporaryDirectory()
        self.state = tempfile.TemporaryDirectory()
        self.blob = os.path.join(self.run.name, "blob.txt")
        with open(self.blob, "w") as f:
            f.write(blob_text(extra=self.PARK_LINE))
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

    def target(self):
        return next(o for o in self.status()["outputs"] if o["channel"] == "9BBB8910/62")["target"]

    def test_control_caps_and_restores(self):
        d = self.start("--control")
        self.assertEqual(d.mode, "control")                          # the throttle output counts as written
        d.cycle(0)
        self.assertEqual((self.fs.read(P0), self.fs.read(P4)), ("2000000", "2000000"))
        self.assertEqual(self.target(), "cpufreq cap")
        self.assertEqual(len(self.status()["throttle"]), 2)
        d.stop()
        self.assertEqual((self.fs.read(P0), self.fs.read(P4)), ("4000000", "4000000"))

    def test_dry_run_leaves_the_files_and_says_so(self):
        d = self.start("--dry-run")
        d.cycle(0)
        self.assertEqual(self.fs.read(P0), "4000000")
        self.assertEqual(self.target(), "cpufreq cap (dry-run)")
        self.assertEqual(self.status()["throttle"][0]["would"], 2000000)
        d.stop()

    def test_emergency_restores_and_returns(self):
        d = self.start("--control")
        d.cycle(0)
        d._enter("emergency", "test")
        self.assertEqual(self.fs.read(P0), "4000000")
        d._write_status()
        self.assertEqual(self.target(), "firmware")
        d._enter("control", "cleared")
        d.cycle(2000)
        self.assertEqual(self.fs.read(P0), "2000000")
        d.stop()

    def test_without_cpufreq_the_output_has_no_linux_path(self):
        import shutil
        shutil.rmtree(self.fs.path("sys/devices/system/cpu/cpufreq"))
        d = self.start("--control")
        self.assertEqual(d.mode, "dry-run")                          # not ready: an output without a Linux path
        d.cycle(0)
        self.assertEqual(self.target(), "no Linux path")
        d.stop()

    def test_foreign_writes_make_it_yield(self):
        d = self.start("--control")
        d.cycle(0)
        for t in (1000, 2000, 3000):
            self.fs.write(P0, 3000000)
            d.cycle(t)
        self.assertEqual(d.state, "yielded")
        self.assertEqual(self.fs.read(P0), "4000000")
        d.stop()


class ThrottleScriptTest(unittest.TestCase):
    """scripts/throttle-test.py on a fake tree: dry-run, refusals and the restore."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "throttle_test", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "throttle-test.py"))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.fs = FakeSys()
        self.fs.add_cpufreq()
        self.fs.add_i915()
        self.fs.write("sys/class/thermal/thermal_zone0/temp", 40000)
        self.fs.write("sys/class/thermal/thermal_zone0/type", "x86_pkg_temp")
        for n in (0, 4):
            self.fs.write(f"sys/devices/system/cpu/cpufreq/policy{n}/scaling_cur_freq", 3900000)
        self.out = tempfile.mkdtemp()
        self.mod.services_active = lambda: []
        self._geteuid = os.geteuid
        os.geteuid = lambda: 0                                      # the module shares `os`: undone in tearDown

    def tearDown(self):
        os.geteuid = self._geteuid
        self.fs.cleanup()

    def run_script(self, *args):
        import contextlib
        buf = io.StringIO()
        old = self.mod.sys.argv
        self.mod.sys.argv = ["throttle-test.py", self.out, "--root", self.fs.root, "--seconds", "1.5", *args]
        try:
            with contextlib.redirect_stdout(buf):
                try:
                    code = self.mod.main()
                except SystemExit as e:
                    code = e.code
        finally:
            self.mod.sys.argv = old
        return code, buf.getvalue()

    def test_dry_run_writes_nothing(self):
        code, out = self.run_script("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry run: nothing written", out)
        self.assertEqual(self.fs.read(P0), "4000000")
        self.assertFalse(os.path.exists(os.path.join(self.out, "throttle.json")))

    def test_run_restores_everything(self):
        code, _ = self.run_script("--pct", "50")
        self.assertEqual(code, 0)
        with open(os.path.join(self.out, "throttle.json")) as f:
            res = json.load(f)
        self.assertEqual(res["restore_errors"], [])
        self.assertEqual(list(res["steps"]["cap_written"].values()), ["2000000", "2000000"])
        self.assertEqual((self.fs.read(P0), self.fs.read(P4), self.fs.read("sys/class/drm/card0/gt_max_freq_mhz")),
                         ("4000000", "4000000", "1100"))

    def test_hot_zone_aborts_and_restores(self):
        self.fs.write("sys/class/thermal/thermal_zone0/temp", 60000)
        code, out = self.run_script("--max-temp", "70")             # 60 C is above 70 - 15: refuses to start
        self.assertNotEqual(code, 0)
        self.assertEqual(self.fs.read(P0), "4000000")
        self.fs.write("sys/class/thermal/thermal_zone0/temp", 50000)
        orig = self.mod.hottest_c
        calls = []

        def hot(root):
            calls.append(1)
            return (50.0, "z") if len(calls) < 3 else (90.0, "z")
        self.mod.hottest_c = hot
        code, out = self.run_script("--max-temp", "80")
        self.mod.hottest_c = orig
        self.assertEqual(code, 2)
        self.assertIn("ABORTED", out)
        self.assertEqual((self.fs.read(P0), self.fs.read(P4)), ("4000000", "4000000"))

    def test_refuses_while_the_service_is_active(self):
        self.mod.services_active = lambda: ["surface-powerd.service"]
        code, _ = self.run_script()
        self.assertIn("refusing", str(code))
        self.assertEqual(self.fs.read(P0), "4000000")


if __name__ == "__main__":
    unittest.main()
