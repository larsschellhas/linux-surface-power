"""The tester kit's whole run on a simulated machine (spec tester-kit.md 6, 7): the chain, what it refuses, and the undo."""
import glob
import io
import json
import os
import tempfile
import unittest

from fakemachine import ENABLED_28, FIRMWARE_OFF, FakeMachine

from surface_power.tools import check
from surface_power.tools.kit import MCHBAR_BY_PLATFORM, chain, result, undo

YES = {"I accept": "I accept", "Collect": "y", "load test": "y", "kernel module": "y", "write test": "write", "unusual": ""}


class KitRun(unittest.TestCase):
    machine_args: dict = {}

    def setUp(self):
        self.m = FakeMachine(**self.machine_args)
        self.work = tempfile.TemporaryDirectory()
        self.patches = self.m.patches(os.path.join(self.work.name, "work"))
        for p in self.patches:
            p.start()
        self.addCleanup(self.m.cleanup)
        self.addCleanup(self.work.cleanup)
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])

    def check_object(self, answers=YES, assume_yes=False):
        asked = []

        def ask(prompt):
            asked.append(prompt)
            for key, value in answers.items():
                if key.lower() in prompt.lower():
                    return value
            return ""
        c = check.Check(self.m.root, assume_yes=assume_yes, out=io.StringIO(), ask_line=ask, release="7.3.6-1-surface")
        c.transport.can_write = lambda: False
        c.asked = asked
        return c

    def go(self, answers=YES):
        c = self.check_object(answers)
        agreed = c.ask_everything()
        interrupted = None if agreed is None else check.run_all(c, agreed)
        return c, agreed, interrupted

    def statuses(self, c):
        return {k: v["status"] for k, v in c.result.stages.items()}

    def assert_everything_undone(self, c):
        self.assertIsNone(self.m.loaded_params, "the module is still loaded")
        self.assertEqual(self.m.units, {u: "active" for u in self.m.units})
        self.assertEqual(self.m.masked, set())
        self.assertEqual(c.undo.pending, [])
        self.assertIsNone(c.undo.leftover())


class FullChain(KitRun):
    def test_every_stage_passes_and_everything_is_undone(self):
        c, _, interrupted = self.go()
        self.assertFalse(interrupted)
        self.assertEqual(self.statuses(c), {s: "passed" for s in ("S1", "S2", "S3", "S4", "S5")}, c.out.getvalue())
        self.assert_everything_undone(c)
        self.assertEqual(self.m.reg(), FIRMWARE_OFF)
        self.assertEqual(c.markers.locks(), {})
        self.assertEqual(glob.glob(os.path.join(self.m.root, chain.MARKERS, "*")), [])

    def test_everything_in_the_result_was_announced_before_the_question(self):
        """A new part of the data must be named in the list the tester agrees to (consent.COLLECTED)."""
        from surface_power.tools.kit import consent
        c, _, _ = self.go()
        parts = set(c.result.data) | {"stages", "tester_note", "sensor_probe", "write"}
        self.assertEqual(parts - set(consent.DISCLOSED) - {"schema"}, set(), "collected but not announced")
        titles = {title for title, _ in consent.COLLECTED}
        self.assertEqual(set(consent.DISCLOSED.values()), titles, "an entry of the list without data, or data under no entry")
        text = " ".join(c.out.getvalue().split())
        for title, what in consent.COLLECTED:
            self.assertIn(title, text)
            self.assertIn(what.split(",")[0], text)
        self.assertLess(text.index("What is collected"), text.index("May the check collect this?"))

    def test_services_are_paused_before_the_load_and_ppd_is_masked(self):
        c, _, _ = self.go()
        verbs = [" ".join(cmd[1:]) for cmd in self.m.commands if cmd[0] == "systemctl" and cmd[1] in ("stop", "start", "mask", "unmask")]
        want = ["stop surface-powerd.service", "mask --runtime power-profiles-daemon.service", "stop power-profiles-daemon.service",
                "unmask --runtime power-profiles-daemon.service", "start power-profiles-daemon.service", "start surface-powerd.service"]
        positions = [verbs.index(w) for w in want]
        self.assertEqual(positions, sorted(positions), verbs)

    def test_what_pausing_and_restarting_change_is_recorded_under_its_own_name(self):
        c, _, _ = self.go()
        events = [(e["stage"], e["new"]) for e in c.result.data["watch"]["events"] if e["what"].endswith("constraint_2_power_limit_uw")]
        self.assertEqual(events, [("pause", "0"), ("restore", "111375000")])

    def test_the_module_is_loaded_read_only_first_and_never_by_force(self):
        self.go()
        loads = [cmd[2:] for cmd in self.m.commands if cmd[0] == "insmod"]
        self.assertEqual(loads, [["allow_write=0"], ["allow_write=1"], ["allow_write=1"]])    # SB3: in the table, no unsafe_platform

    def test_the_first_write_on_a_disabled_copy_only_lowers(self):
        c, _, _ = self.go()
        d = c.result.stages["S5"]["details"]
        self.assertEqual(c.result.data["load"]["limits_switched_on"], {"MSR PL1": 30.0, "platform PL1": 25.0})
        self.assertEqual(d["target_w"], 8)
        self.assertEqual([w["file"] for w in d["writes"]], ["constraint_0_power_limit_uw", "enabled"])
        self.assertEqual(d["register_after"], d["register_before"])
        self.assertAlmostEqual(d["plateau_limited"]["mean_w"], 8)

    def test_the_result_is_one_file_with_a_json_block(self):
        c, _, _ = self.go()
        c.preflight_lines = ["device: x"]
        path = c.save(self.work.name)
        with open(path) as f:
            text = f.read()
        block = text.split(result.MARKER)[1].split("```json\n")[1].split("\n```")[0]
        data = json.loads(block)
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["stages"]["S5"]["status"], "passed")
        self.assertIn("limits", data)
        self.assertIn("watch", data)
        self.assertNotIn("somewhere", text)                 # the builder named in /proc/version is not taken over
        self.assertLess(len(text), result.LIMIT)


class MsrModule(KitRun):
    """Not every system loads `msr` (plan B19: a Laptop 4 on an Arch-based kernel read ENOENT on every register)."""

    def test_msr_is_loaded_for_the_run_and_gone_before_the_services_start_again(self):
        os.remove(self.m.fs.path("dev/cpu/0/msr"))
        c, _, _ = self.go()
        self.assertEqual(self.statuses(c)["S5"], "passed", c.out.getvalue())
        self.assertIn("value", c.result.data["limits"]["msr"]["0x610"])
        commands = [" ".join(cmd) for cmd in self.m.commands]
        self.assertEqual(commands.count("modprobe msr"), 1)
        # a daemon that ran without msr must not find it at its restart and lose it right after
        self.assertLess(commands.index("modprobe -r msr"), commands.index("systemctl start surface-powerd.service"))
        self.assertFalse(os.path.exists(self.m.fs.path("dev/cpu/0/msr")))
        self.assert_everything_undone(c)
        self.assertEqual([e for e in c.result.data["watch"]["events"] if e["what"].startswith("msr ")], [])

    def test_msr_that_is_there_is_left_alone(self):
        c, _, _ = self.go()
        self.assertEqual([cmd for cmd in self.m.commands if cmd[0] == "modprobe" and cmd[-1] == "msr"], [])
        self.assertTrue(os.path.exists(self.m.fs.path("dev/cpu/0/msr")))


class EnabledCopy(KitRun):
    machine_args = {"register": ENABLED_28, "hold_w": None}

    def test_one_write_below_the_enabled_copy(self):
        self.m.fs.write("sys/class/dmi/id/product_name", "Surface Pro 10 for Business")
        self.m.fs.write("sys/class/dmi/id/product_sku", "Surface_Pro_10_for_Business_2079")
        # a Pro 10 is Meteor Lake: its MCHBAR is 0xfedc0000
        from unittest import mock
        with mock.patch.dict(MCHBAR_BY_PLATFORM, {"meteorlake": 0xFED10000}):
            c, _, _ = self.go()
        self.assertEqual(self.statuses(c)["S5"], "passed", c.out.getvalue())
        d = c.result.stages["S5"]["details"]
        self.assertEqual((d["target_w"], [w["file"] for w in d["writes"]]), (23, ["constraint_0_power_limit_uw"]))
        loads = [cmd[2:] for cmd in self.m.commands if cmd[0] == "insmod"]
        self.assertEqual(loads, [["allow_write=0"], ["allow_write=1"], ["allow_write=1"]])    # in the table since 28.11.2026
        self.assertIn("passed on 1 machine of this model (issue #3)", c.out.getvalue())


class Consent(KitRun):
    def test_without_the_disclaimer_nothing_runs(self):
        for answer in ("", "yes", "y"):
            with self.subTest(answer):
                c, agreed, _ = self.go({**YES, "I accept": answer})
                self.assertIsNone(agreed)
                self.assertEqual([cmd for cmd in self.m.commands if cmd[0] in ("insmod", "systemd-run", "modprobe")], [])

    def test_yes_option_never_accepts_disclaimer_module_or_writing(self):
        c = self.check_object({}, assume_yes=True)
        self.assertIsNone(c.ask_everything())
        c = self.check_object({"I accept": "I accept"}, assume_yes=True)
        agreed = c.ask_everything()
        self.assertEqual(agreed, {"read": True, "load": True, "module": False, "write": False})

    def test_writing_needs_the_word(self):
        c, agreed, _ = self.go({**YES, "write test": "y"})
        self.assertFalse(agreed["write"])
        self.assertEqual(self.statuses(c)["S5"], "skipped")
        self.assertEqual(self.statuses(c)["S4"], "passed")
        self.assertEqual([cmd for cmd in self.m.commands if cmd[0] == "insmod" and "allow_write=1" in cmd].__len__(), 1)

    def test_a_new_model_hears_that_the_tester_is_one_of_the_first(self):
        c = self.check_object()
        c.evidence.passed = []                       # independent of what the repository knows by now
        check.run_all(c, c.ask_everything())
        self.assertIn("YOU ARE ONE OF THE FIRST", c.out.getvalue())
        self.assertIn("It has not passed on any machine yet", " ".join(c.out.getvalue().split()))

    def test_a_known_model_hears_how_often_each_step_passed(self):
        c = self.check_object()
        c.evidence.passed = [{"stage": s, "sku": "Surface_Book_3_1899", "model": "Surface Book 3", "platform": "icelake", "issue": 9}
                             for s in ("S2", "S3", "S3")]
        c.ask_everything()
        text = " ".join(c.out.getvalue().split())
        self.assertIn("S3: passed on 2 machines of this model (issue #9)", text)
        self.assertIn("S4: YOU ARE ONE OF THE FIRST", text)


class Chain(KitRun):
    def test_a_register_not_restored_fails_locks_and_ends_the_chain(self):
        self.m.unload_leaves = FIRMWARE_OFF | 1 << 15
        c, _, _ = self.go()
        self.assertEqual(self.statuses(c), {"S1": "passed", "S2": "passed", "S3": "failed", "S4": "skipped", "S5": "skipped"})
        self.assertIn("Something unexpected happened", c.result.next_steps())
        self.m.unload_leaves = None
        self.m.set_reg(FIRMWARE_OFF)
        again, agreed, _ = self.go()
        self.assertFalse(agreed["module"])
        self.assertEqual(self.statuses(again)["S3"], "skipped")
        self.assertIn("unexpected result on this machine", again.result.stages["S3"]["summary"])

    def test_a_refused_module_changes_nothing_and_locks_nothing(self):
        self.m.insmod_error = "insmod: ERROR: Key was rejected by service"
        c, _, _ = self.go()
        self.assertEqual(self.statuses(c)["S3"], "skipped")
        self.assertIn("rejected", "\n".join(c.result.stages["S3"]["details"]["kernel"]))
        self.assertEqual(c.markers.locks(), {})
        self.assert_everything_undone(c)

    def test_a_write_that_touches_other_bits_stops_and_restores(self):
        self.m.write_touches = 1 << 31 >> 8                 # bit 23: the time window
        c, _, _ = self.go()
        self.assertEqual(self.statuses(c)["S5"], "failed")
        self.assertIn("changed other bits", c.result.stages["S5"]["summary"])
        self.assertEqual(self.m.reg(), FIRMWARE_OFF)
        self.assert_everything_undone(c)
        self.assertIn("S5", c.markers.locks())
        self.assertNotIn("S4", c.markers.locks())

    def test_a_write_without_effect_is_not_passed_and_not_locked(self):
        self.m.write_has_no_effect = True
        c, _, _ = self.go()
        self.assertEqual(self.statuses(c)["S5"], "not-passed")
        self.assertIn("no or too little effect", c.result.stages["S5"]["summary"])
        self.assertEqual(c.markers.locks(), {})

    def test_a_new_kernel_warning_fails_the_stage(self):
        real = self.m.run

        def with_warning(cmd, timeout=120, **kw):
            out = real(cmd, timeout, **kw)
            if cmd[0] == "insmod":
                self.m.journal.append("WARNING: CPU: 3 PID: 1 at drivers/powercap/intel_rapl_common.c:99")
            return out
        from surface_power.tools.kit import sh
        from unittest import mock
        with mock.patch.object(sh, "run", with_warning):
            c, _, _ = self.go()
        self.assertEqual(self.statuses(c)["S3"], "failed")

    def test_thermal_throttle_lines_are_not_a_problem(self):
        self.m.journal.append("mce: CPU0: Core temperature above threshold, cpu clock is throttled")
        c, _, _ = self.go()
        self.assertEqual(self.statuses(c)["S5"], "passed")

    def test_the_stop_list_blocks_a_stage_and_those_after_it(self):
        c = self.check_object()
        c.evidence.blocked_entries = [{"stage": "S4", "reason": "found a problem"}]
        check.run_all(c, c.ask_everything())
        self.assertEqual((self.statuses(c)["S3"], self.statuses(c)["S4"], self.statuses(c)["S5"]), ("passed", "skipped", "skipped"))
        self.assertIn("found a problem", c.result.stages["S4"]["summary"])

    def test_a_service_that_cannot_be_stopped_keeps_the_write_stages_off(self):
        self.m.units["thermald-like"] = "active"            # a writer the kit does not know how to stop
        c, _, _ = self.go()
        self.assertEqual((self.statuses(c)["S3"], self.statuses(c)["S4"]), ("passed", "skipped"))
        self.assertIn("still running", c.result.stages["S4"]["summary"])


class Interrupts(KitRun):
    def interrupt_at_load_run(self, number):
        runs = []

        def maybe():
            runs.append(1)
            if len(runs) == number:
                raise KeyboardInterrupt
        self.m.on_load_run = maybe

    def test_ctrl_c_during_the_load_restores_the_services(self):
        self.interrupt_at_load_run(1)
        c, _, interrupted = self.go()
        self.assertTrue(interrupted)
        self.assert_everything_undone(c)
        self.assertEqual(c.result.stages["S2"]["status"], "not-passed")
        self.assertIn("interrupted", c.result.stages["S2"]["summary"])
        self.assertIn("only need to post this file if something looked wrong", c.result.next_steps())

    def test_ctrl_c_during_the_first_write_unloads_and_leaves_no_marker(self):
        self.interrupt_at_load_run(2)
        c, _, interrupted = self.go()
        self.assertTrue(interrupted)
        self.assertEqual(self.m.reg(), FIRMWARE_OFF)
        self.assert_everything_undone(c)
        self.assertEqual(c.markers.locks(), {})

    def test_a_kit_that_dies_leaves_the_marker_and_the_undo_script(self):
        """SIGKILL: no code of the kit runs any more. What it had written before is what protects the machine."""
        seen = {}

        def die():
            if self.m.loaded_params is not None:
                with open(os.path.join(self.m.root, undo.SCRIPT)) as f:
                    seen["script"] = f.read()
                seen["markers"] = sorted(os.listdir(os.path.join(self.m.root, chain.MARKERS)))
        self.m.on_load_run = die
        self.go()
        self.assertEqual(seen["markers"], ["unfinished-S3", "unfinished-S4", "unfinished-S5"])
        self.assertRegex(seen["script"], r"rm -rf -- \S*surface-power-check\b")        # the dry-run's directory
        self.assertRegex(seen["script"], r"rm -rf -- \S*surface-power-check-\w+")      # the module's build directory
        lines = [ln for ln in seen["script"].splitlines() if " || rc=1" in ln]
        self.assertIn("rmmod surface_rapl_mmio", lines[0], lines)                       # newest first: the module goes first
        self.assertIn("systemctl start surface-powerd.service || rc=1", lines)
        self.assertLess(lines.index("systemctl unmask --runtime power-profiles-daemon.service || rc=1"),
                        lines.index("systemctl start power-profiles-daemon.service || rc=1"))

    def test_a_leftover_marker_keeps_its_stage_and_later_ones_off(self):
        chain.Markers(self.m.root).begin("S4")
        c, _, _ = self.go()
        self.assertEqual((self.statuses(c)["S3"], self.statuses(c)["S4"], self.statuses(c)["S5"]), ("passed", "skipped", "skipped"))
        self.assertIn("did not finish", c.result.stages["S4"]["summary"])

    def test_a_run_killed_during_the_first_write_locks_only_that_write(self):
        """The case of the SB3 run of 27.11.2026: markers for S3, S4 and S5, but S3 and S4 had finished."""
        dead = chain.Markers(self.m.root)
        for stage in ("S3", "S4", "S5"):
            dead.begin(stage)
        c, agreed, _ = self.go({**YES, "load test": "n"})
        self.assertEqual((self.statuses(c)["S3"], self.statuses(c)["S4"], self.statuses(c)["S5"]), ("passed", "passed", "skipped"))
        self.assertIn("did not finish during the first write", c.result.stages["S5"]["summary"])
        self.assertIn("First write test: locked", c.out.getvalue())
        self.assertEqual(sorted(chain.Markers(self.m.root).locks()), ["S5"])        # still locked after this run

    def test_a_bug_in_the_kit_still_undoes_and_keeps_the_result(self):
        from unittest import mock
        with mock.patch.object(check.Check, "observe_under_load", side_effect=RuntimeError("boom")):
            c, _, interrupted = self.go()
        self.assertFalse(interrupted)
        self.assert_everything_undone(c)
        self.assertIn("boom", "\n".join(c.result.notes))


if __name__ == "__main__":
    unittest.main()
