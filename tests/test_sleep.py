"""Restore before sleep (plan B16, spec supervisor 7): the signal wakes the loop, the daemon confirms the pause after the
restore, and the sleep hook waits for that confirmation."""
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from fakesys import FakeSys
from test_daemon import DaemonTest

from surface_power import daemon

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
HOOK = os.path.join(HERE, "..", "packaging", "system-sleep", "surface-power")


class WaitTest(unittest.TestCase):
    def test_a_signal_ends_the_wait_at_once(self):
        wake = daemon.Wakeup()
        self.addCleanup(wake.close)
        old = signal.signal(signal.SIGUSR2, lambda *_: None)
        self.addCleanup(signal.signal, signal.SIGUSR2, old)
        wake.install()
        killer = subprocess.Popen([sys.executable, "-c", f"import os,time; time.sleep(0.2); os.kill({os.getpid()}, 12)"])
        t0 = time.monotonic()
        wake.wait(5.0)
        self.assertLess(time.monotonic() - t0, 2.0)
        killer.wait()
        t0 = time.monotonic()
        wake.wait(0.2)                                              # drained: the next wait lasts its time again
        self.assertGreaterEqual(time.monotonic() - t0, 0.18)


class PauseConfirmationTest(DaemonTest):
    """Only the tests defined here run (the base class is used for its fake system)."""

    def test_the_status_says_paused_after_the_restore_and_the_delay_is_logged(self):
        buf = io.StringIO()
        d = self.start("--control", stream=buf)
        d.cycle(0)
        self.assertEqual(self.status()["state"], "control")
        d.before_sleep(signalled=time.monotonic() - 0.05)
        self.assertEqual(self.status()["state"], "paused")          # written at once, without another cycle
        self.assertEqual(self.fs.read("sys/class/powercap/intel-rapl:1/constraint_0_power_limit_uw"), "25000000")
        self.assertRegex(buf.getvalue(), r"paused: firmware values restored, \d+ ms after the sleep signal")
        d.after_resume(1000)
        d.cycle(1000)
        self.assertEqual(self.status()["state"], "control")
        d.stop()


    def test_a_second_sleep_signal_does_not_leave_it_paused(self):
        """The unit before nvidia-suspend and the sleep hook both signal: the resume must still return to control."""
        d = self.start("--control")
        d.cycle(0)
        d.before_sleep()
        d.before_sleep()
        d.after_resume(1000)
        d.cycle(1000)
        self.assertEqual(d.state, "control")
        d.after_resume(2000)                                        # both resume signals, too
        d.cycle(2000)
        self.assertEqual(d.state, "control")
        d.stop()


# unittest would run every inherited DaemonTest method again: keep only the new one
for _name in [n for n in dir(DaemonTest) if n.startswith("test_")]:
    if _name not in PauseConfirmationTest.__dict__:
        setattr(PauseConfirmationTest, _name, None)
del DaemonTest


class LoopTest(unittest.TestCase):
    """The real loop in a process of its own: SIGUSR2 must lead to 'paused' well inside a fraction of a tick."""

    def test_pause_follows_the_signal_without_waiting_for_the_tick(self):
        from test_daemon import blob_text
        fs = FakeSys()
        self.addCleanup(fs.cleanup)
        with tempfile.TemporaryDirectory() as run, tempfile.TemporaryDirectory() as state:
            blob = os.path.join(state, "blob.csv")
            with open(blob, "w") as f:
                f.write(blob_text())
            p = subprocess.Popen([sys.executable, "-m", "surface_power.daemon", "--dry-run", "--no-ec", "--root", fs.root,
                                  "--blob", blob, "--run-dir", run, "--state-dir", state],
                                 env=dict(os.environ, PYTHONPATH=SRC), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                def current():
                    try:
                        with open(os.path.join(run, "status.json")) as f:
                            return json.load(f)["state"]
                    except (OSError, ValueError):
                        return None

                def until(state_name, seconds):
                    end = time.monotonic() + seconds
                    while time.monotonic() < end:
                        if current() == state_name:
                            return True
                        time.sleep(0.005)
                    return False

                self.assertTrue(until("dry-run", 15))
                delays = []
                for _ in range(4):
                    time.sleep(0.15)                                # somewhere inside a tick
                    t0 = time.monotonic()
                    p.send_signal(signal.SIGUSR2)
                    self.assertTrue(until("paused", 3))
                    delays.append(time.monotonic() - t0)
                    p.send_signal(signal.SIGUSR1)
                    self.assertTrue(until("dry-run", 5))
                    time.sleep(0.2)
                self.assertLess(max(delays), 0.4, delays)           # waiting for the 1 s tick gives up to 1 s
            finally:
                p.send_signal(signal.SIGTERM)
                p.wait(15)


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = os.path.join(self.tmp.name, "run")
        os.makedirs(self.run_dir)
        self.calls = os.path.join(self.tmp.name, "calls")
        stub = os.path.join(self.tmp.name, "systemctl")
        with open(stub, "w") as f:
            f.write('#!/bin/sh\necho "$@" >> "$CALLS"\n'
                    'case "$1" in is-active) [ "$3" = "$ACTIVE" ] ;; kill) '
                    '[ -n "$CONFIRM_AFTER" ] && (sleep "$CONFIRM_AFTER"; '
                    'printf \'{"state": "paused"}\' > "$SURFACE_POWER_RUN_DIR/status.json") >/dev/null 2>&1 & ;; esac\n')
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
        self.write_status("control")

    def write_status(self, state):
        with open(os.path.join(self.run_dir, "status.json"), "w") as f:
            f.write(json.dumps({"state": state, "controllers": [{"state": "OFF"}]}, indent=1))

    def hook(self, phase, active="surface-powerd.service", confirm_after="0.4"):
        env = dict(os.environ, PATH=self.tmp.name + os.pathsep + os.environ["PATH"], CALLS=self.calls, ACTIVE=active,
                   CONFIRM_AFTER=confirm_after, SURFACE_POWER_RUN_DIR=self.run_dir)
        t0 = time.monotonic()
        p = subprocess.run(["sh", HOOK, phase], env=env, capture_output=True, text=True, timeout=30)
        log = open(self.calls).read() if os.path.exists(self.calls) else ""
        return p, time.monotonic() - t0, log

    def test_waits_until_the_daemon_confirms(self):
        p, took, calls = self.hook("pre")
        self.assertEqual(p.returncode, 0)
        self.assertIn("kill --signal=SIGUSR2 surface-powerd.service", calls)
        self.assertGreaterEqual(took, 0.4)
        self.assertLess(took, 3.0)
        self.assertNotIn("did not confirm", p.stdout)

    def test_gives_up_after_its_limit_and_says_so(self):
        p, took, _ = self.hook("pre", confirm_after="")
        self.assertEqual(p.returncode, 0)                            # a sleep is never blocked
        self.assertGreaterEqual(took, 4.5)
        self.assertLess(took, 12.0)
        self.assertIn("surface-powerd.service did not confirm the pause", p.stdout)

    def test_a_missing_confirmation_records_what_the_daemon_was_doing(self):
        p, _, _ = self.hook("pre", confirm_after="")
        self.assertIn("surface-power: surface-powerd.service state:", p.stdout)
        self.assertIn("wchan", p.stdout)

    def test_no_wait_without_a_running_daemon(self):
        p, took, calls = self.hook("pre", active="none")
        self.assertEqual(p.returncode, 0)
        self.assertLess(took, 1.0)
        self.assertNotIn("SIGUSR2", calls)

    def test_after_the_resume(self):
        p, _, calls = self.hook("post")
        self.assertEqual(p.returncode, 0)
        self.assertIn("kill --signal=SIGUSR1 surface-powerd.service", calls)


class PreSleepUnitTest(unittest.TestCase):
    """The pause has to happen before nvidia-suspend.service: after it an NVML call hangs until the resume, and the sleep
    hook only runs after it [M, SB3, 26.11.2026]."""

    def test_unit(self):
        path = os.path.join(HERE, "..", "packaging", "systemd", "surface-power-sleep.service")
        with open(path) as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        before = " ".join(line[len("Before="):] for line in lines if line.startswith("Before=")).split()
        for unit in ("sleep.target", "nvidia-suspend.service", "nvidia-hibernate.service",
                     "nvidia-suspend-then-hibernate.service"):
            self.assertIn(unit, before)
        for line in ("StopWhenUnneeded=yes", "Type=oneshot", "RemainAfterExit=yes", "WantedBy=sleep.target",
                     "ExecStart=/usr/lib/systemd/system-sleep/surface-power pre",
                     "ExecStop=/usr/lib/systemd/system-sleep/surface-power post"):
            self.assertIn(line, lines)


if __name__ == "__main__":
    unittest.main()
