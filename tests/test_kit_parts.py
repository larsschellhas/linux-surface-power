"""The tester kit's parts: signing cases, the undo with its watchdog (real /bin/sh), load analysis, registers, evidence."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from fakemachine import FIRMWARE_OFF
from fakesys import FakeSys, MCHBAR, RAPL_UNITS

from surface_power.adapters import inputs as inp
from surface_power.loader.model import ChannelRef
from surface_power.tools.kit import chain, load, module, result, sh, signing, system, undo, watch, write


class Signing(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        self.addCleanup(self.fs.cleanup)

    def decide(self, enforce, mokutil=None, key=False, lockdown="[none] integrity confidentiality"):
        self.fs.write("sys/kernel/security/lockdown", lockdown)
        self.fs.write("sys/module/module/parameters/sig_enforce", enforce)
        if key:
            self.fs.write("var/lib/shim-signed/mok/MOK.priv", "k")
            self.fs.write("var/lib/shim-signed/mok/MOK.der", "c")
        with mock.patch.object(sh, "run", lambda cmd, timeout=120, **kw: (1, mokutil or "")):
            return signing.decide(self.fs.root)

    def test_not_enforced_needs_no_key(self):
        self.assertEqual(self.decide("N").case, "A")              # the Pro 7+ report: Secure Boot on, enforcement off

    def test_enrolled_key_although_mokutil_exits_with_1(self):
        self.assertEqual(self.decide("Y", "MOK.der is already enrolled", key=True).case, "B")

    def test_key_that_is_not_enrolled(self):
        s = self.decide("Y", "MOK.der is not enrolled", key=True)
        self.assertEqual((s.case, s.usable), ("C", False))

    def test_unknown_enrolment_lets_insmod_decide(self):
        self.assertEqual(self.decide("Y", "mokutil: not found", key=True).case, "B")

    def test_no_key_and_lockdown(self):
        self.assertEqual(self.decide("Y").case, "D")
        self.assertEqual(self.decide("N", lockdown="none [integrity] confidentiality").case, "E")


class UndoWithRealShell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.u = undo.Undo(self.tmp.name)

    def f(self, name):
        return os.path.join(self.tmp.name, name)

    def watchdog_script(self, pid, max_s):
        with mock.patch.object(sh, "run", lambda cmd, timeout=120, **kw: (0, "")):
            ok, _ = self.u.start_watchdog(pid, max_s)
        self.assertTrue(ok)
        return os.path.join(self.tmp.name, undo.WATCH)

    def test_the_script_holds_the_pending_commands_newest_first(self):
        self.u.push(["touch", self.f("first")])
        second = self.u.push(["touch", self.f("it's second")])
        subprocess.run(["/bin/sh", os.path.join(self.tmp.name, undo.SCRIPT)], check=True)
        self.assertTrue(os.path.exists(self.f("first")) and os.path.exists(self.f("it's second")))
        self.u.drop(second)
        with open(os.path.join(self.tmp.name, undo.SCRIPT)) as f:
            self.assertNotIn("second", f.read())

    def test_the_watchdog_undoes_after_the_kit_is_killed(self):
        kit = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.u.push(["touch", self.f("undone")])
        dog = subprocess.Popen(["/bin/sh", self.watchdog_script(kit.pid, 60)])
        try:
            time.sleep(1)
            self.assertFalse(os.path.exists(self.f("undone")), "ran while the kit was alive")
            kit.kill()
            kit.wait()
            dog.wait(timeout=15)
        finally:
            kit.kill()
            dog.kill()
        self.assertTrue(os.path.exists(self.f("undone")))
        self.assertIsNone(self.u.leftover())
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, undo.WATCH)), "the watchdog left its own script")

    def test_only_the_kits_own_directories_can_be_removed(self):
        self.assertEqual(undo.remove_tree("/run/surface-power-check"), ["rm", "-rf", "--", "/run/surface-power-check"])
        self.assertTrue(undo.remove_tree("/tmp/surface-power-check-ab12"))
        for path in ("/", "/run", "/home/x", "/tmp/surface-power", ""):
            with self.subTest(path), self.assertRaises(ValueError):
                undo.remove_tree(path)

    def test_the_watchdog_undoes_when_the_run_takes_too_long(self):
        self.u.push(["touch", self.f("undone")])
        subprocess.run(["/bin/sh", self.watchdog_script(os.getpid(), 1)], check=True, timeout=20)
        self.assertTrue(os.path.exists(self.f("undone")))

    def test_a_failing_undo_command_does_not_stop_the_others_and_keeps_the_script(self):
        self.u.push(["touch", self.f("older")])
        self.u.push(["false"])
        code = subprocess.run(["/bin/sh", os.path.join(self.tmp.name, undo.SCRIPT)]).returncode
        self.assertEqual(code, 1)
        self.assertTrue(os.path.exists(self.f("older")))

    def test_undo_commands_succeed_when_there_is_nothing_left_to_undo(self):
        modules = self.f("sys-module")
        os.makedirs(modules)
        self.assertEqual(subprocess.run(undo.unload_module("surface_rapl_mmio", modules)).returncode, 0)
        self.assertEqual(subprocess.run(undo.stop_unit("surface-power-no-such-unit.service")).returncode, 0)
        os.makedirs(os.path.join(modules, "surface_rapl_mmio"))
        fake = self.f("bin")
        os.makedirs(fake)
        with open(os.path.join(fake, "rmmod"), "w") as f:            # a module that will not go: the failure must show
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(fake, "rmmod"), 0o755)
        env = {**os.environ, "PATH": fake + os.pathsep + os.environ["PATH"]}
        self.assertEqual(subprocess.run(undo.unload_module("surface_rapl_mmio", modules), env=env).returncode, 1)

    def test_nothing_pending_leaves_no_script_and_a_second_kit_is_refused(self):
        cmd = self.u.push(["true"])
        self.u.drop(cmd)
        self.assertIsNone(self.u.leftover())
        self.u.lock()
        with self.assertRaises(undo.Busy):
            undo.Undo(self.tmp.name).lock()

    def test_a_service_that_cannot_be_stopped_is_not_masked_or_restarted(self):
        calls = []

        def run(cmd, timeout=120, **kw):
            calls.append(cmd)
            return (1, "no") if cmd[1] == "stop" else (0, "")
        with mock.patch.object(sh, "run", run):
            paused, failed = self.u.pause_services({"tuned": {"active": "active", "enabled": "enabled"},
                                                    "thermald": {"active": "inactive", "enabled": "enabled"}})
        self.assertEqual((paused, failed, self.u.pending), ([], ["tuned"], []))
        self.assertIn(["systemctl", "unmask", "--runtime", "tuned.service"], calls)
        self.assertFalse(any("thermald.service" in c for c in calls))

    def test_a_unit_the_tester_had_masked_stays_masked(self):
        with mock.patch.object(sh, "run", lambda cmd, timeout=120, **kw: (0, "")):
            self.u.pause_services({"tuned": {"active": "active", "enabled": "masked"}})
            self.assertEqual(self.u.pending, [["systemctl", "start", "tuned.service"]])


class Registers(unittest.TestCase):
    def test_window_of_the_measured_registers(self):
        # field 0x6e = 27,983,872 us on the SB3 and the Pro 10 [M]
        self.assertEqual(module.Register(0x0000811800DD80E0, RAPL_UNITS, MCHBAR).window_us(), 27983872)

    def test_what_must_not_be_loaded_on(self):
        ok = module.Register(FIRMWARE_OFF, RAPL_UNITS, MCHBAR)
        self.assertIsNone(ok.implausible())
        self.assertEqual((ok.copy.pl1[0], ok.copy.pl2[0]), (25, 61))
        for raw, unit, word in ((FIRMWARE_OFF, 0, "unit register"), (FIRMWARE_OFF | 1 << 31, RAPL_UNITS, "locked"),
                                (FIRMWARE_OFF | 1 << 63, RAPL_UNITS, "locked"), (0, RAPL_UNITS, "PL1 reads 0"),
                                (0xFFFFFFFF7FFF7FFF & ~(1 << 63 | 1 << 31), RAPL_UNITS, "outside")):
            with self.subTest(word):
                self.assertIn(word, module.Register(raw, unit, MCHBAR).implausible())

    def test_wrong_mchbar_is_not_read(self):
        fs = FakeSys()
        self.addCleanup(fs.cleanup)
        fs.add_mmio_register(FIRMWARE_OFF)
        reg, why = module.Register.read(fs.root, "tigerlake")
        self.assertIsNone(reg)
        self.assertIn("differs", why)
        out = system.mmio_registers(fs.root, "tigerlake")
        self.assertIsNone(out["limit"])
        self.assertEqual(system.mmio_registers(fs.root, "icelake")["limit"], f"{FIRMWARE_OFF:#018x}")

    def test_target_is_below_every_limit_or_nothing(self):
        self.assertEqual(write.target(30, 25, 13.5)[0], 8)           # the SB3 without the daemon
        self.assertEqual(write.target(35, 28, 28.0)[0], 23)          # a Pro 10
        self.assertEqual(write.target(30, 30, 29.6)[0], 24)
        for msr_w, mmio_w, plateau in ((30, 30, 9.5), (10, 25, 30), (30, 9, 30)):
            self.assertIsNone(write.target(msr_w, mmio_w, plateau)[0])
        for msr_w, mmio_w, plateau in [(a, b, c / 2) for a in range(8, 80, 3) for b in range(8, 80, 3) for c in range(10, 160, 7)]:
            watts, _ = write.target(msr_w, mmio_w, plateau)
            if watts is not None:
                self.assertTrue(8 <= watts <= min(msr_w, mmio_w) - 3 and watts <= plateau - 3, (msr_w, mmio_w, plateau, watts))

    def test_a_disabled_copy_with_a_high_pl2_is_lowered_before_it_is_switched_on(self):
        fs = FakeSys()
        self.addCleanup(fs.cleanup)
        high = FIRMWARE_OFF & ~(0x7FFF << 32) | (90 * 8) << 32                   # PL2 90 W, MSR PL2 61 W
        fs.add_mmio_register(high)
        order = []

        def write_zone(root, name, value):
            order.append((name, value))
            r = module.Register.read(root, "icelake")[0].raw
            if name == "enabled":
                r |= (3 << 15) | (3 << 47)
            else:
                shift = 0 if name.startswith("constraint_0") else 32
                r = r & ~(0x7FFF << shift) | int(value / 1e6 * 8) << shift
            fs.add_mmio_register(r)
        from surface_power.adapters import rapl_copies
        msr_copy = rapl_copies.Copy((61 * 8 << 32) | 30 * 8, 0.125)
        before = module.Register.read(fs.root, "icelake")[0]
        with mock.patch.object(write, "_write_zone", write_zone):
            write.apply(fs.root, "icelake", 8, before, msr_copy, [])
        self.assertEqual(order, [("constraint_1_power_limit_uw", 61_000_000), ("constraint_0_power_limit_uw", 8_000_000),
                                 ("enabled", 1)])


class KernelLines(unittest.TestCase):
    def test_what_counts_as_trouble(self):
        for line in ("WARNING: CPU: 3 PID: 12 at drivers/powercap/intel_rapl_common.c:99 f+0x1/0x2",
                     "WARNING: drivers/base/core.c:12 at device_release+0x1/0x2, CPU#1: rmmod/99",
                     "BUG: unable to handle page fault for address: ffff", "Oops: 0000 [#1] PREEMPT SMP",
                     "general protection fault, probably for non-canonical address", "Call Trace:",
                     "watchdog: BUG: soft lockup - CPU#2 stuck", "task insmod:12 blocked for more than 122 seconds.",
                     "mce: [Hardware Error]: Machine check events logged"):
            with self.subTest(line):
                self.assertTrue(module.BAD_LINE.search(line))
        for line in ("ACPI Warning: \\_SB.PC00: Argument #4 type mismatch", "iwlwifi: warning: firmware retried",
                     "msr: Write to unrecognized MSR 0x615 by surface-powerd, tainting CPU_OUT_OF_SPEC.",
                     "surface_rapl_mmio: device taken from the unsafe_platform parameter, not from the opt-in table",
                     "mce: CPU0: Core temperature above threshold, cpu clock is throttled"):
            with self.subTest(line):
                self.assertFalse(module.BAD_LINE.search(line) and not module.HARMLESS.search(line))


class LoadAnalysis(unittest.TestCase):
    def samples(self, watts, psys=None):
        return [{"t": i, "phase": "load", "pkg_w": w, **({"psys_w": psys} if psys else {})} for i, w in enumerate(watts, 1)]

    def test_plateau_and_the_limit_it_sits_on(self):
        top = load.plateau(self.samples([40] * 30 + [28.2, 27.9] * 40))
        self.assertTrue(top["steady"])
        self.assertAlmostEqual(top["mean_w"], 28.05, 1)
        self.assertIn("at MMIO PL1 28 W", load.in_force(top, {"MSR PL1": 35, "MMIO PL1": 28}))

    def test_something_else_holding_it_is_inconclusive(self):
        top = load.plateau(self.samples([13.5] * 90, psys=25.1))
        self.assertIn("at platform PL1 25 W", load.in_force(top, {"MSR PL1": 30, "platform PL1": 25}))
        self.assertIn("inconclusive", load.in_force(top, {"MSR PL1": 30}))

    def test_no_verdict_from_too_few_or_unsteady_samples(self):
        self.assertIsNone(load.plateau(self.samples([20] * 10)))
        unsteady = load.plateau(self.samples([15, 35] * 45))
        self.assertFalse(unsteady["steady"])
        self.assertIn("did not settle", load.in_force(unsteady, {"MSR PL1": 25}))

    def run_phases(self, sample):
        clock = [0.0]
        stopped = []

        class FakeLoad:
            def start(self, seconds):
                pass

            def stop(self):
                stopped.append(clock[0])

        class FakeSampler:
            def sample(self):
                return dict(sample(clock[0]))
        got = load.run_phases((("load", 100, True), ("cooldown", 5, False)), FakeSampler(), FakeLoad(),
                              [ChannelRef(inp.TEMP, 509)], clock=lambda: clock[0],
                              sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        return got, stopped

    def test_a_hot_skin_sensor_ends_the_load_after_10_s(self):
        got, stopped = self.run_phases(lambda t: {"cpu_c": 80, "sam9_c": 61})
        self.assertIn("skin temperature", got["aborted"])
        self.assertLess(stopped[0], 15)

    def test_a_hot_cpu_ends_it_and_100_degrees_does_not(self):
        self.assertIn("CPU temperature", self.run_phases(lambda t: {"cpu_c": 106})[0]["aborted"])
        self.assertIsNone(self.run_phases(lambda t: {"cpu_c": 100, "sam9_c": 45})[0]["aborted"])

    def test_a_blind_load_is_ended(self):
        self.assertIn("not readable", self.run_phases(lambda t: {})[0]["aborted"])

    def test_the_load_processes_end_by_themselves(self):
        p = subprocess.Popen([sys.executable, "-I", "-c", load.WORKER, "1", str(os.getpid())])
        self.assertEqual(p.wait(timeout=20), 0)

    def test_the_load_ends_when_the_kit_is_killed(self):
        """Real processes: a stand-in for the kit starts the load and is killed with SIGKILL long before the deadline."""
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        kit = subprocess.Popen([sys.executable, "-c",
                                "import sys, time\n"
                                f"sys.path.insert(0, {src!r})\n"
                                "from surface_power.tools.kit import load\n"
                                "w = load.Load(); w.start(120, cpus=2)\n"
                                "print(*[p.pid for p in w.procs], flush=True)\n"
                                "time.sleep(120)\n"], stdout=subprocess.PIPE, text=True)

        def running(pid):
            try:                                                   # a finished process may wait for its new parent as a zombie
                with open(f"/proc/{pid}/stat") as f:
                    return f.read().rsplit(")", 1)[1].split()[0] != "Z"
            except OSError:
                return False
        try:
            pids = [int(x) for x in kit.stdout.readline().split()]
            time.sleep(0.5)
            self.assertTrue(len(pids) == 2 and all(running(p) for p in pids), "the load did not start")
            kit.kill()
            kit.wait()
            deadline = time.monotonic() + 10
            while any(running(p) for p in pids) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(any(running(p) for p in pids), "the load outlived the kit")
        finally:
            kit.kill()
            kit.stdout.close()

    def test_sampler_power_from_the_energy_counter_with_a_wrap(self):
        fs = FakeSys()
        self.addCleanup(fs.cleanup)
        zone = "sys/class/powercap/intel-rapl:0"
        fs.write(f"{zone}/energy_uj", 990_000_000)
        fs.write(f"{zone}/max_energy_range_uj", 1_000_000_000)
        clock = [10.0]
        s = load.Sampler(fs.root, clock=lambda: clock[0])
        self.assertIsNone(s.sample()["pkg_w"])
        clock[0] += 2
        fs.write(f"{zone}/energy_uj", 30_000_000)
        self.assertEqual(s.sample()["pkg_w"], 20.0)


class WatchAndRecords(unittest.TestCase):
    def test_the_watch_records_a_foreign_change(self):
        fs = FakeSys()
        self.addCleanup(fs.cleanup)
        w = watch.Watch(fs.root)
        w.sample()
        fs.write("sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw", 0)
        w.stage = "S2"
        w.sample()
        self.assertEqual([(e["stage"], e["what"], e["new"]) for e in w.events],
                         [("S2", "intel-rapl:0/constraint_2_power_limit_uw", "0")])

    def test_markers_own_run_does_not_lock_itself_but_the_next_one(self):
        with tempfile.TemporaryDirectory() as root:
            mine = chain.Markers(root)
            mine.begin("S3")
            self.assertEqual(mine.locks(), {})
            self.assertEqual(sorted(chain.Markers(root).locks()), ["S3", "S4", "S5"])
            mine.run_ended_cleanly()
            self.assertEqual(chain.Markers(root).locks(), {})

    def test_markers_of_several_dead_runs_and_unreadable_ones(self):
        with tempfile.TemporaryDirectory() as root:
            first = chain.Markers(root)
            for stage in ("S3", "S4", "S5"):
                first.begin(stage)
            self.assertEqual(sorted(chain.Markers(root).locks()), ["S5"])
            second = chain.Markers(root)                 # a later run that died early: it overwrote S3's marker
            second.begin("S3")
            self.assertEqual(sorted(chain.Markers(root).locks()), ["S3", "S4", "S5"])
            second.run_ended_cleanly()                   # or it ended well: the first run's lock is still there
            self.assertEqual(sorted(chain.Markers(root).locks()), ["S5"])
            with open(os.path.join(root, chain.MARKERS, "unfinished-S4"), "w") as f:
                f.write("{cut off")                      # unreadable: counts for itself
            self.assertEqual(sorted(chain.Markers(root).locks()), ["S4", "S5"])
            chain.Markers(root).failed("S3", "x")
            self.assertEqual(sorted(chain.Markers(root).locks()), ["S3", "S4", "S5"])

    def test_stop_list_scopes(self):
        e = chain.Evidence("/nonexistent")
        e.blocked_entries = [{"stage": "S5", "platform": "alderlake", "reason": "a"}, {"stage": "S4", "sku": "X", "reason": "b"}]
        self.assertEqual((e.blocked("S5", "Y", "alderlake"), e.blocked("S5", "Y", "icelake"), e.blocked("S4", "X", None)),
                         ("a", None, "b"))

    def test_the_repository_file_is_valid(self):
        e = chain.Evidence()
        self.assertTrue(e.passed)
        for p in e.passed:
            self.assertIn(p["stage"], ("S1", "S2", "S3", "S4", "S5"))
            self.assertTrue(p["sku"] and p["model"] and p["platform"])

    def test_a_long_result_is_thinned_to_fit_and_the_full_one_kept(self):
        r = result.Result("t")
        r.data["load"] = {"samples": result.table([{"t": i, "pkg_w": 12.345, "cpu_c": 77.0, "freq_mhz": 3300} for i in range(4000)])}
        short, full = r.finish({})
        self.assertLess(len(short), result.LIMIT)
        self.assertGreater(len(full), result.LIMIT)
        data = json.loads(short.split(result.MARKER)[1].split("```json\n")[1].split("\n```")[0])
        self.assertTrue(data["thinned"])

    def test_the_builder_in_proc_version_and_other_cmdline_options_stay_private(self):
        fs = FakeSys()
        self.addCleanup(fs.cleanup)
        fs.write("proc/version", "Linux version 7.3 (alice@alices-box) (gcc 13) #1")
        fs.write("proc/cmdline", "BOOT_IMAGE=/vmlinuz root=UUID=1234 cryptdevice=abcd lockdown=none intel_pstate=active quiet")
        state = system.platform_state(fs.root, "7.3.6-1-surface")
        self.assertEqual((state["compiler"], state["cmdline"]), ("gcc", ["lockdown=none", "intel_pstate=active"]))
        self.assertNotIn("alice", json.dumps(state))


if __name__ == "__main__":
    unittest.main()
