"""Tests for the dGPU clock-cap stand-in (spec outputs 6, deviation DO-1)."""
import os
import unittest

from fakesys import FakeSys

from surface_power.adapters import gpu

PCI = "0000:02:00.0"


class FakeNvml:
    """Stands in for the NVML wrapper. Records sessions and caps; `power` and `clock` are what the GPU reports."""

    def __init__(self, power=62.0, clock=1500, default_limit=65.0, fail=False):
        self.power, self.clock, self.default_limit, self.fail = power, clock, default_limit, fail
        self.sessions = 0
        self.cap = None             # None: no cap set
        self.min_clock, self.max_clock = 300, 2100

    def __enter__(self):
        if self.fail:
            raise gpu.NvmlError("no library")
        self.sessions += 1
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return gpu.Sample(self.power, self.clock, self.default_limit, self.min_clock, self.max_clock)

    def set_cap(self, max_mhz):
        self.cap = max_mhz

    def reset_cap(self):
        self.cap = None


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


class StandInTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        dev = f"sys/bus/pci/devices/{PCI}"
        self.fs.write(f"{dev}/vendor", "0x10de")
        self.fs.write(f"{dev}/class", "0x030200")
        self.nvml = FakeNvml()
        self.pids = []

    def tearDown(self):
        self.fs.cleanup()

    def stand_in(self, dry_run=False):
        # Probe without caching: these tests look at the control law, not at the scan interval (ProbeTest).
        return gpu.ClockCapStandIn(self.fs.root, lambda pci: self.nvml, dry_run=dry_run,
                                   probe=gpu.ClientProbe(self.fs.root, PCI, interval_ms=0))

    def client(self, pid=4242, device="/dev/nvidia0"):
        """Another process with an NVIDIA device open (what keeps the GPU awake)."""
        self.fs.link_raw(f"proc/{pid}/fd/7", device)
        self.pids.append(pid)

    def no_clients(self):
        import shutil
        for pid in self.pids:
            shutil.rmtree(self.fs.path(f"proc/{pid}"), ignore_errors=True)
        self.pids = []

    def test_finds_the_nvidia_gpu(self):
        self.assertEqual(self.stand_in().pci, PCI)

    def test_never_opens_nvml_without_another_client(self):
        s = self.stand_in()
        self.fs.link_raw("proc/99/fd/3", "/dev/null")                   # other open files do not count
        self.fs.link_raw(f"proc/{os.getpid()}/fd/9", "/dev/nvidia0")   # nor does the daemon itself
        s.step(0, 50.0)
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.sessions, 0)

    def test_regulates_while_a_client_runs(self):
        s = self.stand_in()
        self.client()
        s.step(0, 50.0)
        self.assertEqual(self.nvml.cap, 1260)
        s.step(1000, 50.0)
        self.assertEqual(self.nvml.sessions, 1)                         # sampled every 2 s only
        self.nvml.power, self.nvml.clock = 50.2, 1260
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.cap, 1260)                           # inside the band: hold
        self.assertEqual(s.status()["cap_mhz"], 1260)

    def test_keeps_the_cap_while_the_gpu_sleeps(self):
        s = self.stand_in()
        self.client()
        s.step(0, 50.0)
        self.no_clients()
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.cap, 1260)

    def test_one_session_while_a_client_is_there(self):
        """NVML init is expensive: one session across samples, closed as soon as the client is gone."""
        s = self.stand_in()
        self.client()
        for t in range(0, 10000, 2000):
            s.step(t, 50.0)
        self.assertEqual(self.nvml.sessions, 1)
        self.assertIsNotNone(s.session)
        self.no_clients()
        s.step(10000, 50.0)
        self.assertIsNone(s.session)

    def test_release(self):
        s = self.stand_in()
        self.client()
        s.step(0, 50.0)
        s.release()
        self.assertIsNone(self.nvml.cap)
        self.assertIsNone(s.status()["cap_mhz"])

    def test_target_at_default_limit_releases(self):
        s = self.stand_in()
        self.client()
        s.step(0, 50.0)
        s.step(2000, 65.0)
        self.assertIsNone(self.nvml.cap)

    def test_dry_run_sets_nothing(self):
        s = self.stand_in(dry_run=True)
        self.client()
        s.step(0, 50.0)
        self.assertIsNone(self.nvml.cap)
        self.assertEqual(s.status()["would_cap_mhz"], 1260)

    def test_nvml_failure_disables_it(self):
        self.nvml.fail = True
        s = self.stand_in()
        self.client()
        s.step(0, 50.0)
        self.assertFalse(s.enabled)
        self.assertIn("no library", s.status()["error"])
        self.nvml.fail = False
        s.step(2000, 50.0)
        self.assertEqual(self.nvml.sessions, 0)

    def test_no_nvidia_gpu(self):
        os.remove(self.fs.path(f"sys/bus/pci/devices/{PCI}/vendor"))
        s = self.stand_in()
        self.assertIsNone(s.pci)
        self.assertFalse(s.enabled)


if __name__ == "__main__":
    unittest.main()


class ProbeTest(unittest.TestCase):
    """The /proc scan for other NVIDIA clients is expensive: none while the GPU sleeps, else at most every 10 s."""

    def setUp(self):
        self.fs = FakeSys()
        self.fs.write(f"sys/bus/pci/devices/{PCI}/power/runtime_status", "active")
        self.p = gpu.ClientProbe(self.fs.root, PCI)
        self.scans = 0
        real = gpu.other_nvidia_client

        def counting(root):
            self.scans += 1
            return real(root)
        self._real, gpu.other_nvidia_client = real, counting

    def tearDown(self):
        gpu.other_nvidia_client = self._real
        self.fs.cleanup()

    def test_scan_interval(self):
        self.assertFalse(self.p.has_client(0))              # GPU awake at start: scanned at once
        self.fs.link_raw("proc/4242/fd/7", "/dev/nvidia0")
        self.assertFalse(self.p.has_client(5000))           # cached
        self.assertTrue(self.p.has_client(10000))           # rescanned after 10 s
        self.assertEqual(self.scans, 2)

    def test_scan_when_the_gpu_wakes_up(self):
        """A client opening the GPU wakes it: that is seen in the same second, not after the interval."""
        self.fs.write(f"sys/bus/pci/devices/{PCI}/power/runtime_status", "suspended")
        self.assertFalse(self.p.has_client(0))
        self.fs.link_raw("proc/4242/fd/7", "/dev/nvidia0")
        self.fs.write(f"sys/bus/pci/devices/{PCI}/power/runtime_status", "active")
        self.assertTrue(self.p.has_client(1000))
        self.assertEqual(self.scans, 1)

    def test_no_scan_while_suspended(self):
        self.fs.write(f"sys/bus/pci/devices/{PCI}/power/runtime_status", "suspended")
        for t in range(0, 60000, 1000):
            self.assertFalse(self.p.has_client(t))
        self.assertEqual(self.scans, 0)


class DgpuSensorsTest(unittest.TestCase):
    """dGPU power and temperature as blob inputs (spec inputs 8.10)."""

    def setUp(self):
        from surface_power.adapters import inputs
        self.inputs = inputs
        self.fs = FakeSys()
        d = f"sys/bus/pci/devices/{PCI}"
        self.fs.write(f"{d}/vendor", "0x10de")
        self.fs.write(f"{d}/class", "0x030200")
        self.fs.write(f"{d}/power/runtime_status", "active")
        self.nv = FakeNvml(power=42.5)
        self.nv.temperature_c = lambda: 61
        self.src = inputs.DgpuSensors(self.fs.root, nvml_factory=lambda pci: self.nv,
                                      probe=gpu.ClientProbe(self.fs.root, PCI, interval_ms=0))
        self.p, self.t = inputs.ChannelRef(inputs.STATE, 3), inputs.ChannelRef(inputs.TEMP, 101)

    def tearDown(self):
        self.fs.cleanup()

    def test_no_session_without_other_client(self):
        v = self.src.read(0)
        self.assertEqual(self.nv.sessions, 0)
        self.assertIsNone(v[self.p])

    def test_reads_while_another_client_keeps_it_awake(self):
        self.fs.link_raw("proc/4242/fd/5", "/dev/nvidia0")
        v = self.src.read(0)
        self.assertEqual((v[self.p], v[self.t]), (42500, 2731 + 610))
        self.src.read(1000)
        self.assertEqual(self.nv.sessions, 1)                  # at most every 2 s

    def test_suspended_gpu_is_zero_watts(self):
        self.fs.write(f"sys/bus/pci/devices/{PCI}/power/runtime_status", "suspended")
        v = self.src.read(0)
        self.assertEqual((v[self.p], v[self.t]), (0, None))
        self.assertEqual(self.nv.sessions, 0)

    def test_without_nvidia_gpu_not_provided(self):
        fs = FakeSys()
        self.assertEqual(self.inputs.DgpuSensors(fs.root).channels, set())
        fs.cleanup()
