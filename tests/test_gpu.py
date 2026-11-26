"""Tests for the dGPU clock-cap stand-in (spec outputs 6, deviation DO-1)."""
import os
import unittest

from fakesys import FakeSys

from surface_power.adapters import gpu

PCI = "0000:02:00.0"


class FakeNvml:
    """Stands in for the NVML wrapper. Records sessions and caps; `power` and `clock` are what the GPU reports."""

    def __init__(self, power=62.0, clock=1500, default_limit=65.0, fail=False, util=99):
        self.power, self.clock, self.default_limit, self.fail = power, clock, default_limit, fail
        self.util = util
        self.fail_read = False
        self.sessions = self.closed = self.reads = 0
        self.set_error = None       # NvmlError raised by set_cap and reset_cap
        self.cap = None             # None: no cap set
        self.min_clock, self.max_clock = 300, 2100

    def __enter__(self):
        if self.fail:
            raise gpu.NvmlError("no library")
        self.sessions += 1
        return self

    def __exit__(self, *exc):
        self.closed += 1
        return False

    def read(self):
        if self.fail_read:
            raise gpu.NvmlError("nvmlDeviceGetPowerUsage returned 15", 15)
        self.reads += 1
        return gpu.Sample(self.power, self.clock, self.default_limit, self.min_clock, self.max_clock, self.util)

    def temperature_c(self):
        return 61

    def max_clock_mhz(self):
        return self.max_clock

    def set_cap(self, max_mhz):
        if self.set_error:
            raise self.set_error
        self.cap = max_mhz

    def reset_cap(self):
        if self.set_error:
            raise self.set_error
        self.cap = None


RUNTIME = f"sys/bus/pci/devices/{PCI}/power/runtime_status"


def add_gpu(fs, status="active"):
    fs.write(f"sys/bus/pci/devices/{PCI}/vendor", "0x10de")
    fs.write(f"sys/bus/pci/devices/{PCI}/class", "0x030200")
    fs.write(RUNTIME, status)


class ControlLawTest(unittest.TestCase):
    def test_lower_when_above_target(self):
        # 62 W against 50 W: 20 MHz/W * 12 W = 240 MHz below the current clock, rounded to 15 MHz.
        self.assertEqual(gpu.next_cap(None, power=62.0, target=50.0, clock=1500, lo=300, hi=2100), 1260)

    def test_at_least_one_step_down(self):
        self.assertEqual(gpu.next_cap(1260, power=50.6, target=50.0, clock=1260, lo=300, hi=2100), 1245)

    def test_hold_inside_band(self):
        self.assertEqual(gpu.next_cap(1260, power=49.0, target=50.0, clock=1260, lo=300, hi=2100), 1260)

    def test_raise_only_when_running_at_the_cap(self):
        self.assertEqual(gpu.next_cap(1260, power=45.0, target=50.0, clock=1255, lo=300, hi=2100), 1275)
        self.assertEqual(gpu.next_cap(1260, power=45.0, target=50.0, clock=900, lo=300, hi=2100), 1260)   # light load

    def test_limits(self):
        self.assertEqual(gpu.next_cap(315, power=80.0, target=10.0, clock=315, lo=300, hi=2100), 300)
        self.assertIsNone(gpu.next_cap(2100, power=20.0, target=50.0, clock=2100, lo=300, hi=2100))   # cap released


class CapabilitiesTest(unittest.TestCase):
    """What an owner is told from the read-only NVML answers (spec outputs 6.1)."""

    def caps(self, **kw):
        base = dict(name="GPU", power_w=10.0, default_limit_w=50.0, enforced_limit_w=50.0, limit_min_w=None, limit_max_w=None,
                    max_clock_mhz=2100)
        base.update(kw)
        return gpu.Capabilities(**base)

    def test_fixed_limit_means_clock_cap_only(self):
        text = " ".join(gpu.assess(self.caps(limit_min_w=50.0, limit_max_w=50.0)))
        self.assertIn("fixed power limit of 50 W", text)
        self.assertIn("only the clock cap", text)

    def test_range_is_no_proof_of_a_settable_limit(self):
        text = " ".join(gpu.assess(self.caps(limit_min_w=1.0, limit_max_w=65.0)))      # what the SB3 reports, and refuses
        self.assertIn("1 to 65 W", text)
        self.assertIn("no proof", text)
        self.assertIn("dgpu-probe.py --try-limit", text)

    def test_no_answer_and_missing_values(self):
        text = " ".join(gpu.assess(self.caps(default_limit_w=None, max_clock_mhz=None)))
        self.assertIn("does not report a power-limit range", text)
        self.assertIn("default power limit is unknown", text)
        self.assertIn("maximum graphics clock is unknown", text)


class GateTest(unittest.TestCase):
    """The idle gate (spec dgpu-standin-service 4): when NVML may be opened, without looking for other processes."""

    def setUp(self):
        self.fs = FakeSys()
        add_gpu(self.fs)
        self.nvml = FakeNvml(power=3.0, util=0)
        self.gate = gpu.GpuGate(self.fs.root, PCI, lambda pci: self.nvml)
        self.gate.target_w = 50.0

    def tearDown(self):
        self.fs.cleanup()

    def run_ms(self, start, end, step=1000):
        for t in range(start, end, step):
            self.gate.poll(t)

    def test_no_nvml_while_suspended(self):
        self.fs.write(RUNTIME, "suspended")
        self.run_ms(0, 120000)
        self.assertEqual((self.nvml.sessions, self.gate.state), (0, "asleep"))

    def test_samples_at_once_on_wake_and_every_two_seconds(self):
        self.fs.write(RUNTIME, "suspended")
        self.gate.poll(0)
        self.fs.write(RUNTIME, "active")
        self.nvml.util = 99
        self.run_ms(1000, 7000)
        self.assertEqual((self.nvml.sessions, self.nvml.reads, self.gate.state), (1, 3, "sampling"))    # 1, 3, 5 s

    def test_quiet_after_five_idle_samples_closes_the_session_while_awake(self):
        self.run_ms(0, 9000)                                    # samples at 0, 2, 4, 6, 8 s
        self.assertEqual((self.nvml.reads, self.gate.state), (5, "sampling"))
        self.assertEqual(self.nvml.closed, 0)                   # the consumers of this sample still have the session
        self.gate.poll(9000)
        self.assertEqual((self.gate.state, self.nvml.closed, self.gate.session), ("quiet", 1, None))
        self.run_ms(10000, 43000)
        self.assertEqual((self.nvml.reads, self.nvml.sessions), (5, 1))       # nothing touches NVML in the pause

    def test_asks_again_after_the_pause_if_still_awake(self):
        self.run_ms(0, 10000)
        self.run_ms(10000, 43000)
        self.assertEqual(self.nvml.reads, 5)
        self.gate.poll(43000)                                   # QUIET_MS = 35 s after the sample at 8 s
        self.assertEqual((self.gate.state, self.nvml.reads), ("sampling", 6))

    def test_pause_ends_in_sleep_and_wake_is_seen_at_once(self):
        self.run_ms(0, 12000)
        self.fs.write(RUNTIME, "suspended")
        self.gate.poll(30000)
        self.assertEqual(self.gate.state, "asleep")
        self.fs.write(RUNTIME, "active")
        self.gate.poll(31000)
        self.assertEqual((self.gate.state, self.nvml.reads), ("sampling", 6))

    def test_busy_or_above_target_is_never_idle(self):
        for power, util in ((3.0, 30), (60.0, 0), (49.0, 0)):   # load; above target; at the target (not below T - 2 W)
            nvml = FakeNvml(power=power, util=util)
            gate = gpu.GpuGate(self.fs.root, PCI, lambda pci: nvml)
            gate.target_w = 50.0
            for t in range(0, 60000, 1000):
                gate.poll(t)
            self.assertEqual((gate.state, nvml.reads), ("sampling", 30), (power, util))

    def test_without_a_target_utilisation_decides(self):
        self.gate.target_w = None
        self.nvml.power = 60.0
        self.run_ms(0, 12000)
        self.assertEqual(self.gate.state, "quiet")

    def test_gone_without_runtime_status_and_back(self):
        self.nvml.util = 99
        self.gate.poll(0)
        os.remove(self.fs.path(RUNTIME))
        self.gate.poll(1000)
        self.assertEqual((self.gate.state, self.gate.session, self.gate.generation), ("gone", None, 1))
        self.run_ms(2000, 20000)
        self.assertEqual(self.nvml.reads, 1)
        self.fs.write(RUNTIME, "active")
        self.gate.poll(21000)
        self.assertEqual((self.gate.state, self.nvml.reads, self.gate.generation), ("sampling", 2, 1))

    def test_an_open_session_is_left_alone_on_a_sleeping_gpu(self):
        """nvmlShutdown wakes a sleeping GPU [M, M2b]: nothing is closed or queried there."""
        self.nvml.util = 99
        self.gate.poll(0)
        self.fs.write(RUNTIME, "suspended")
        self.run_ms(1000, 20000)
        self.assertEqual((self.nvml.closed, self.nvml.reads), (0, 1))


class StandInTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        add_gpu(self.fs)
        self.nvml = FakeNvml()

    def tearDown(self):
        self.fs.cleanup()

    def stand_in(self, dry_run=False):
        return gpu.ClockCapStandIn(self.fs.root, lambda pci: self.nvml, dry_run=dry_run)

    def events(self, s):
        return " | ".join(f"{p}: {t}" for p, t in s.drain_events())

    def test_finds_the_nvidia_gpu(self):
        self.assertEqual(self.stand_in().pci, PCI)

    def test_regulates_without_looking_for_other_processes(self):
        """B15: under the unit the daemon cannot see other users' processes; the stand-in must not depend on it."""
        s = self.stand_in()
        s.step(0, 50.0)
        self.assertEqual(self.nvml.cap, 1260)
        s.step(1000, 50.0)
        self.assertEqual(self.nvml.reads, 1)                            # sampled every 2 s only
        self.nvml.power, self.nvml.clock = 50.2, 1260
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.cap, 1260)                           # inside the band: hold
        st = s.status()
        self.assertEqual((st["cap_mhz"], st["gate"], st["acting"], st["reason"]), (1260, "sampling", True, None))
        self.assertIn("first clock cap set: 1260 MHz", self.events(s))

    def test_never_wakes_a_sleeping_gpu(self):
        self.fs.write(RUNTIME, "suspended")
        s = self.stand_in()
        s.step(0, 50.0)
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.sessions, 0)

    def test_keeps_the_cap_through_pause_and_sleep(self):
        s = self.stand_in()
        s.step(0, 50.0)
        self.nvml.power, self.nvml.util, self.nvml.clock = 3.0, 0, 300
        for t in range(2000, 30000, 1000):
            s.step(t, 50.0)
        self.assertEqual((s.status()["gate"], self.nvml.cap, s.status()["cap_mhz"]), ("quiet", 1260, 1260))

    def test_forgets_the_cap_when_the_gpu_is_gone(self):
        s = self.stand_in()
        s.step(0, 50.0)
        os.remove(self.fs.path(RUNTIME))
        s.step(1000, 50.0)
        self.assertEqual((s.status()["gate"], s.status()["cap_mhz"], s.enabled), ("gone", None, True))
        self.fs.write(RUNTIME, "active")
        self.nvml.cap = None                                            # the re-added device has no cap
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.cap, 1260)

    def test_release(self):
        s = self.stand_in()
        s.step(0, 50.0)
        s.release()
        self.assertIsNone(self.nvml.cap)
        self.assertIsNone(s.status()["cap_mhz"])

    def test_target_at_default_limit_releases(self):
        s = self.stand_in()
        s.step(0, 50.0)
        s.step(2000, 65.0)
        self.assertIsNone(self.nvml.cap)

    def test_dry_run_sets_nothing(self):
        s = self.stand_in(dry_run=True)
        s.step(0, 50.0)
        self.assertIsNone(self.nvml.cap)
        st = s.status()
        self.assertEqual((st["would_cap_mhz"], st["acting"], st["reason"]), (1260, False, "dry-run"))

    def test_no_permission_switches_it_off_and_says_so(self):
        """What the installed unit gets from nvmlDeviceSetGpuLockedClocks [M, B15]."""
        self.nvml.set_error = gpu.NvmlError("nvmlDeviceSetGpuLockedClocks returned 4", 4)
        s = self.stand_in()
        s.step(0, 50.0)
        st = s.status()
        self.assertEqual((s.enabled, st["acting"]), (False, False))
        self.assertIn("returned 4", st["reason"])
        text = self.events(s)
        self.assertIn("warning: dGPU stand-in off for this run", text)
        s.step(2000, 50.0)
        self.assertEqual((self.events(s), self.nvml.reads, s.session), ("", 1, None))

    def test_a_failed_release_names_the_cap_that_may_remain(self):
        s = self.stand_in()
        s.step(0, 50.0)
        s.drain_events()
        self.nvml.set_error = gpu.NvmlError("nvmlDeviceResetGpuLockedClocks returned 4", 4)
        s.release()
        self.assertIn("a clock cap of 1260 MHz may still be set", self.events(s))

    def test_a_transient_failure_at_release_does_not_end_the_stand_in(self):
        """Seen on the SB3 around a system sleep: one time-out while releasing switched the stand-in off for good."""
        s = self.stand_in()
        s.step(0, 50.0)
        s.drain_events()
        self.nvml.set_error = gpu.NvmlError("clock helper: unreachable: timed out")
        s.release()
        self.assertEqual((s.enabled, s.cap), (True, 1260))              # still ours to take back
        self.assertIn("clock cap of 1260 MHz not removed yet", self.events(s))
        self.nvml.set_error = None
        s.release()
        self.assertEqual((s.cap, self.nvml.cap), (None, None))

    def test_five_failed_releases_in_a_row_end_it(self):
        s = self.stand_in()
        s.step(0, 50.0)
        self.nvml.set_error = gpu.NvmlError("nvml returned 999", 999)
        for _ in range(5):
            s.release()
        self.assertFalse(s.enabled)

    def test_after_a_system_sleep_the_cap_is_sent_again(self):
        s = self.stand_in()
        s.step(0, 50.0)
        self.nvml.power, self.nvml.clock = 50.2, 1260                   # inside the band: the law would hold
        self.nvml.cap = None                                            # what the sleep may have done to it [H]
        s.after_sleep()
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.cap, 1260)
        self.nvml.cap = 900
        s.after_sleep()
        s.step(4000, 65.0)                                              # a target without a cap: still reset
        self.assertIsNone(self.nvml.cap)

    def test_transient_errors_are_retried_and_end_it_after_five_in_a_row(self):
        s = self.stand_in()
        self.nvml.fail_read = False
        s.step(0, 50.0)
        self.nvml.fail_read = True
        for t in range(2000, 10000, 2000):                              # four failures
            s.step(t, 50.0)
        self.assertTrue(s.enabled)
        self.nvml.fail_read = False
        s.step(10000, 50.0)                                             # a good sample resets the count
        self.nvml.fail_read = True
        for t in range(12000, 22000, 2000):                             # five in a row
            s.step(t, 50.0)
        self.assertFalse(s.enabled)
        self.assertIn("returned 15", s.status()["error"])

    def test_missing_library_switches_it_off_at_once(self):
        def factory(pci):
            raise gpu.NvmlError("libnvidia-ml.so.1 not loadable", permanent=True)
        s = gpu.ClockCapStandIn(self.fs.root, factory)
        s.step(0, 50.0)
        self.assertFalse(s.enabled)
        self.assertIn("not loadable", s.status()["error"])

    def test_warns_once_when_the_cap_has_no_effect(self):
        s = self.stand_in()                                             # the fake GPU stays at 62 W whatever is set
        for t in range(0, 29000, 1000):
            s.step(t, 50.0)
        self.assertNotIn("no effect", self.events(s))
        for t in range(29000, 90000, 1000):
            s.step(t, 50.0)
        self.assertEqual(self.events(s).count("the clock cap has no effect"), 1)

    def test_no_nvidia_gpu(self):
        os.remove(self.fs.path(f"sys/bus/pci/devices/{PCI}/vendor"))
        s = self.stand_in()
        self.assertIsNone(s.pci)
        self.assertFalse(s.enabled)


class DgpuSensorsTest(unittest.TestCase):
    """dGPU power and temperature as blob inputs (spec inputs 8.10), through the same gate."""

    def setUp(self):
        from surface_power.adapters import inputs
        self.inputs = inputs
        self.fs = FakeSys()
        add_gpu(self.fs)
        self.nv = FakeNvml(power=42.5)
        self.src = inputs.DgpuSensors(self.fs.root, nvml_factory=lambda pci: self.nv)
        self.p, self.t = inputs.ChannelRef(inputs.STATE, 3), inputs.ChannelRef(inputs.TEMP, 101)

    def tearDown(self):
        self.fs.cleanup()

    def test_reads_an_awake_gpu(self):
        v = self.src.read(0)
        self.assertEqual((v[self.p], v[self.t]), (42500, 2731 + 610))
        self.src.read(1000)
        self.assertEqual(self.nv.reads, 1)                     # at most every 2 s

    def test_suspended_gpu_is_zero_watts(self):
        self.fs.write(RUNTIME, "suspended")
        v = self.src.read(0)
        self.assertEqual((v[self.p], v[self.t]), (0, None))
        self.assertEqual(self.nv.sessions, 0)

    def test_pause_keeps_the_last_sample_and_gone_is_invalid(self):
        self.nv.power, self.nv.util = 3.0, 0
        for t in range(0, 20000, 1000):
            v = self.src.read(t)
        self.assertEqual((self.src.gate.state, v[self.p], self.nv.reads), ("quiet", 3000, 5))
        os.remove(self.fs.path(RUNTIME))
        v = self.src.read(20000)
        self.assertEqual((v[self.p], v[self.t]), (None, None))

    def test_stand_in_and_sensors_share_one_gate(self):
        s = gpu.ClockCapStandIn(self.fs.root, lambda pci: self.nv, gate=self.src.gate)
        self.nv.power = 62.0
        self.src.read(0)
        s.step(0, 50.0)
        self.assertEqual((self.nv.reads, self.nv.sessions, self.nv.cap), (1, 1, 1260))

    def test_without_nvidia_gpu_not_provided(self):
        fs = FakeSys()
        self.assertEqual(self.inputs.DgpuSensors(fs.root).channels, set())
        fs.cleanup()


if __name__ == "__main__":
    unittest.main()
