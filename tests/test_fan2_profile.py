"""scripts/fan2-profile-test.py (Laptop Studio 2, linux-surface#2392), all on fakes: no device is opened."""
import contextlib
import importlib.util
import io
import json
import os
import signal
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load_script():
    spec = importlib.util.spec_from_file_location("fan2_profile_test", os.path.join(HERE, "..", "scripts", "fan2-profile-test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeTransport:
    path = "/fake/aggregator"

    def __init__(self):
        self.writes, self.fail_writes, self.writable = [], False, True

    def can_write(self):
        return self.writable

    def send(self, tc, cid, iid, payload, tid=1):
        if self.fail_writes:
            return False
        self.writes.append((tc, tid, cid, iid, bytes(payload)))
        return True


class FileTransport:
    """Used by the watchdog process."""

    def __init__(self, path):
        self.path = path

    def send(self, tc, cid, iid, payload, tid=1):
        with open(self.path, "a") as f:
            f.write(f"{cid:02x} {iid} {bytes(payload).hex()}\n")
        return True


class FakeEnv:
    lock_path = "/fake/lock"

    def __init__(self, log_file):
        self.root = "/nonexistent"
        self.transport = FakeTransport()
        self.log_file = log_file
        self.t, self.pkg, self.profile = 0.0, 50.0, "balanced"
        self.name, self.root_ok, self.ac_v, self.services = "Surface Laptop Studio 2", True, True, []
        self.choices = ["low-power", "balanced", "balanced-performance", "performance"]
        self.profiles_set, self.reads = [], []
        self.answers = {0x01: bytes.fromhex("b80b"), 0x0D: bytes(4), 0x05: bytes(100)}
        self.answer_yes, self.on_tick = True, lambda env: None

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.on_tick(self)

    def is_root(self):
        return self.root_ok

    def product(self):
        return self.name

    def kernel(self):
        return "0"

    def ac(self):
        return self.ac_v

    def services_active(self):
        return self.services

    def temps(self):
        return None, self.pkg

    def thermals(self):
        return {"temp9": 33.0}

    def platform_profile(self):
        return self.profile

    def profile_choices(self):
        return self.choices

    def set_profile(self, name):
        self.profiles_set.append(name)
        self.profile = name
        return True

    def read_fan(self, cid, iid):
        self.reads.append((cid, iid))
        return self.answers.get(cid)

    def uptime_s(self):
        return 300

    def other_services(self):
        return {}

    def confirm(self, _question):
        return self.answer_yes

    def take_lock(self):
        return True

    def release_lock(self):
        pass

    def transport_factory(self):
        return FileTransport(self.log_file)


class Fan2ProfileScriptTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_script()
        self.mod.WAIT_S = 3
        self.out = tempfile.mkdtemp()
        self.watchdog_log = os.path.join(self.out, "watchdog.log")
        self.env = FakeEnv(self.watchdog_log)
        self.addCleanup(lambda: [signal.signal(s, signal.SIG_DFL) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)])

    def run_script(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                code = self.mod.main([self.out, *args], self.env)
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def result(self):
        with open(os.path.join(self.out, "fan2-profile.json")) as f:
            return json.load(f)

    def values(self):
        return [w[4][0] for w in self.env.transport.writes]

    def assert_nothing_touched(self):
        self.assertEqual((self.env.transport.writes, self.env.profiles_set, self.env.reads), ([], [], []))

    def test_dry_run_prints_the_frames_and_opens_nothing(self):
        code, out = self.run_script("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("050102000e010001", out)          # FAN 0x0e, instance 2, value 1
        self.assertIn("050101010d0000", out)            # read 0x0d, instance 1
        self.assert_nothing_touched()

    def test_full_run_writes_only_the_profile_of_fan_2_and_ends_at_the_start_value(self):
        code, _ = self.run_script()
        self.assertEqual(code, 0)
        self.assertEqual({w[:4] for w in self.env.transport.writes}, {(0x05, 1, 0x0E, 2)})
        self.assertEqual(self.values(), [1, 3, 4, 2, 2])          # start value last, then the restore
        self.assertEqual(self.env.profiles_set,
                         ["low-power", "balanced", "balanced-performance", "performance", "balanced", "balanced"])
        r = self.result()
        self.assertEqual([s["phase"] for s in r["steps"]], ["A"] + ["B"] * 4 + ["C"] * 5)
        self.assertEqual(r["restore_errors"], [])
        self.assertFalse(os.path.exists(self.watchdog_log))       # clean stop: the watchdog sends nothing
        with open(os.path.join(self.out, "fan2-profile.md")) as f:
            md = f.read()
        self.assertIn("| B: sysfs performance (4) | 2 | 3000 |", md)
        self.assertIn("050102000e010004", md)

    def test_refuses_without_the_conditions(self):
        for field, value, text in (("name", "Surface Book 3", "Laptop Studio 2 only"), ("root_ok", False, "needs root"),
                                   ("ac_v", False, "AC power"), ("pkg", 75.0, "start limit"),
                                   ("services", ["surface-powerd.service"], "is active"),
                                   ("choices", ["balanced"], "choices missing")):
            with self.subTest(field):
                self.env = FakeEnv(self.watchdog_log)
                setattr(self.env, field, value)
                code, _ = self.run_script()
                self.assertIn(text, str(code))
                self.assert_nothing_touched()

    def test_no_confirmation_no_run(self):
        self.env.answer_yes = False
        code, _ = self.run_script()
        self.assertEqual(code, "not started")
        self.assert_nothing_touched()

    def test_hot_cpu_aborts_and_restores(self):
        def heat(env):
            if env.transport.writes:
                env.pkg = 90.0
        self.env.on_tick = heat
        code, _ = self.run_script()
        self.assertEqual(code, 2)
        self.assertEqual(self.values(), [1, 2])                   # first write, then the restore
        self.assertEqual(self.env.profile, "balanced")
        self.assertIn("limit 85", self.result()["aborted"])

    def test_foreign_profile_change_stops_the_run(self):
        def meddle(env):
            if env.profiles_set == ["low-power"]:
                env.profile = "performance"
        self.env.on_tick = meddle
        code, _ = self.run_script()
        self.assertEqual(code, 2)
        self.assertEqual(self.env.transport.writes, [])           # fan 2 was never written, so no restore frame for it
        self.assertEqual(self.env.profiles_set[-1], "balanced")

    def test_failed_write_to_fan_2_reports_the_failed_restore_and_leaves_it_to_the_watchdog(self):
        self.env.transport.fail_writes = True
        code, _ = self.run_script()
        self.assertEqual(code, 1)
        self.assertTrue(self.result()["restore_errors"])
        with open(self.watchdog_log) as f:
            self.assertEqual(f.read().split(), ["0e", "2", "02"])

    def test_a_read_without_an_answer_stops_after_three_tries(self):
        del self.env.answers[0x05]
        code, _ = self.run_script()
        self.assertEqual(code, 0)
        self.assertEqual(self.env.reads.count((0x05, 2)), 3)
        self.assertEqual(self.env.reads.count((0x0D, 2)), 10)

    def test_guards(self):
        for value in (0, 5):
            with self.assertRaises(self.mod.ft.Refuse):
                self.mod.profile_frame(value)
        with self.assertRaises(self.mod.ft.Refuse):
            self.mod.read_frame(0x0F, 1)


if __name__ == "__main__":
    unittest.main()
