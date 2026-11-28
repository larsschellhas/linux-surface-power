"""Tests for the clock helper surface-gpu-clockd, its client, the marker and the reset tool (spec dgpu-standin-service 5, 6)."""
import contextlib
import io
import os
import socket
import unittest

from fakehelper import HelperThread
from fakesys import FakeSys
from test_gpu import RUNTIME, FakeNvml, add_gpu

from surface_power import gpuclockd
from surface_power.adapters import gpu, gpuclock
from surface_power.tools import reset


class Base(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSys()
        add_gpu(self.fs)
        self.nvml = FakeNvml()
        self.path = self.fs.path("gpu.sock")
        self.helper = None

    def tearDown(self):
        if self.helper:
            self.helper.stop()
        self.fs.cleanup()

    def start(self, **kw):
        self.helper = HelperThread(self.path, self.fs.root, self.nvml, **kw)
        return gpuclock.ClockHelper(self.path)

    def raw(self, data: bytes) -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(self.path)
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)
            return s.recv(200)


class ProtocolTest(Base):
    def test_ping_does_not_open_nvml(self):
        self.start().ping()
        self.assertEqual(self.nvml.sessions, 0)

    def test_cap_and_reset_one_session_per_request(self):
        c = self.start()
        c.set_cap(1290)
        self.assertEqual((self.nvml.cap, self.nvml.sessions, self.nvml.closed), (1290, 1, 1))
        c.reset_cap()
        self.assertEqual((self.nvml.cap, self.nvml.sessions, self.nvml.closed), (None, 2, 2))

    def test_range(self):
        c = self.start()
        for mhz in (299, 0, 2101, 99999):
            with self.assertRaises(gpuclock.HelperError) as e:
                c.set_cap(mhz)
            self.assertEqual((e.exception.kind, e.exception.permanent), ("range", True), mhz)
        self.assertIsNone(self.nvml.cap)
        c.set_cap(300)
        c.set_cap(2100)
        self.assertEqual(self.nvml.cap, 2100)

    def test_anything_else_is_refused(self):
        self.start()
        for data in (b"", b"ping", b"PING\n", b"ping \n", b"cap\n", b"cap -5\n", b"cap 12.5\n", b"cap 0x500\n", b"cap 1200 \n",
                     b"cap 123456\n", b"reset now\n", b"reset\nreset\n", b"ping\nx", b"x" * 33, b"x" * 5000,
                     b"\xff\xfe\n", b"cap \xd9\xa1\xd9\xa2\xd9\xa3\xd9\xa4\n"):
            self.assertTrue(self.raw(data).startswith(b"error request "), data[:40])
        self.assertEqual(self.nvml.sessions, 0)
        self.assertEqual(self.raw(b"ping\n"), b"ok 1\n")                  # still serving

    def test_a_peer_that_is_not_allowed_is_refused(self):
        c = self.start(allowed_uids={os.getuid() + 1})
        with self.assertRaises(gpuclock.HelperError) as e:
            c.set_cap(1200)
        self.assertEqual(e.exception.kind, "request")
        self.assertEqual(self.nvml.sessions, 0)

    def test_only_root_by_default_when_run_as_root(self):
        self.assertEqual(gpuclockd.default_allowed_uids(0), {0})
        self.assertEqual(gpuclockd.default_allowed_uids(1000), {0, 1000})       # run by hand in a test

    def test_a_silent_client_is_dropped(self):
        c = self.start(client_timeout=0.2)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(self.path)
            c.ping()                                                    # served after the silent one was dropped

    def test_error_kinds(self):
        c = self.start()
        for code, kind, permanent in ((4, "permission", True), (15, "no-gpu", False), (999, "nvml", False)):
            self.nvml.set_error = gpu.NvmlError(f"call returned {code}", code)
            with self.assertRaises(gpuclock.HelperError) as e:
                c.set_cap(1200)
            self.assertEqual((e.exception.kind, e.exception.permanent), (kind, permanent), code)
        self.nvml.set_error = RuntimeError("unexpected")
        with self.assertRaises(gpuclock.HelperError) as e:
            c.reset_cap()
        self.assertEqual(e.exception.kind, "nvml")
        self.nvml.set_error = None
        c.set_cap(1200)                                                  # the server survived all of it

    def test_no_gpu(self):
        c = self.start()
        os.remove(self.fs.path("sys/bus/pci/devices/0000:02:00.0/vendor"))
        with self.assertRaises(gpuclock.HelperError) as e:
            c.set_cap(1200)
        self.assertEqual(e.exception.kind, "no-gpu")

    def test_no_helper(self):
        with self.assertRaises(gpuclock.HelperError) as e:
            gpuclock.ClockHelper(self.path).ping()
        self.assertEqual((e.exception.kind, e.exception.permanent), ("unreachable", False))


class StandInWithHelperTest(Base):
    """The stand-in reads through NVML itself and sends the cap to the helper; a marker covers a killed daemon."""

    def stand_in(self, client, **kw):
        self.reader = FakeNvml()                          # the daemon's session: it may not set anything
        self.reader.set_error = gpu.NvmlError("nvmlDeviceSetGpuLockedClocks returned 4", 4)
        self.marker = self.fs.path("gpu-cap")
        return gpu.ClockCapStandIn(self.fs.root, lambda pci: self.reader, setter=client, marker=self.marker, **kw)

    def test_cap_goes_through_the_helper_and_leaves_a_marker(self):
        s = self.stand_in(self.start())
        s.step(0, 50.0)
        self.assertEqual((self.nvml.cap, s.status()["cap_mhz"], s.enabled), (1260, 1260, True))
        self.assertTrue(os.path.exists(self.marker))
        s.release()
        self.assertIsNone(self.nvml.cap)
        self.assertFalse(os.path.exists(self.marker))

    def test_release_resets_when_only_the_marker_says_so(self):
        s = self.stand_in(self.start())
        self.nvml.cap = 900
        open(self.marker, "w").close()                    # left by a killed predecessor
        s.release()
        self.assertIsNone(self.nvml.cap)
        self.assertFalse(os.path.exists(self.marker))

    def test_nothing_is_sent_without_a_cap_or_marker(self):
        s = self.stand_in(self.start())
        s.release()
        self.assertEqual(self.nvml.sessions, 0)

    def test_dry_run_sends_nothing(self):
        s = self.stand_in(self.start(), dry_run=True)
        s.step(0, 50.0)
        s.release()
        self.assertEqual((self.nvml.sessions, os.path.exists(self.marker)), (0, False))

    def test_helper_gone_is_retried_then_ends_the_stand_in(self):
        s = self.stand_in(gpuclock.ClockHelper(self.path))
        for t in range(0, 8000, 2000):
            s.step(t, 50.0)
        self.assertTrue(s.enabled)
        s.step(8000, 50.0)
        self.assertFalse(s.enabled)
        self.assertIn("unreachable", s.status()["reason"])

    def test_a_gone_gpu_resets_the_error_count_and_the_marker(self):
        client = self.start()
        s = self.stand_in(client)
        s.step(0, 50.0)
        os.remove(self.fs.path(RUNTIME))
        s.step(1000, 50.0)
        self.assertEqual((s.status()["cap_mhz"], os.path.exists(self.marker), s.set_failures), (None, False, 0))

    def test_permanent_error_tries_once_to_take_the_cap_back(self):
        s = self.stand_in(self.start())
        s.step(0, 50.0)
        self.reader.fail_read = True
        for t in range(2000, 12000, 2000):                # the daemon's reading fails five times
            s.step(t, 50.0)
        self.assertFalse(s.enabled)
        self.assertEqual((self.nvml.cap, os.path.exists(self.marker)), (None, False))

    def test_unwritable_marker_means_no_cap(self):
        client = self.start()
        s = self.stand_in(client)
        s.marker = self.fs.path("missing-directory/gpu-cap")
        s.step(0, 50.0)
        self.assertEqual((self.nvml.cap, s.enabled), (None, False))


class ResetToolTest(Base):
    def run_reset(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = reset.main(["--run-dir", self.fs.path("run"), "--gpu-clock-socket", self.path])
        return code, out.getvalue()

    def test_marker_resets_the_cap(self):
        self.start()
        self.nvml.cap = 1200
        self.fs.write("run/gpu-cap", "")
        code, out = self.run_reset()
        self.assertEqual((code, self.nvml.cap, os.path.exists(self.fs.path("run/gpu-cap"))), (0, None, False))
        self.assertIn("GPU clock cap removed", out)

    def test_without_marker_the_helper_is_not_asked(self):
        self.start()
        os.makedirs(self.fs.path("run"))
        code, _ = self.run_reset()
        self.assertEqual((code, self.nvml.sessions), (0, 0))

    def test_failure_keeps_the_marker_and_still_restores_the_rest(self):
        self.fs.write("run/gpu-cap", "")
        code, out = self.run_reset()                       # no helper
        self.assertEqual(code, 1)
        self.assertTrue(os.path.exists(self.fs.path("run/gpu-cap")))
        self.assertIn("GPU clock cap not removed", out)
        self.assertIn("no snapshot", out)


class UnitFilesTest(unittest.TestCase):
    ROOT = os.path.join(os.path.dirname(__file__), "..", "packaging", "systemd")

    def lines(self, name):
        with open(os.path.join(self.ROOT, name)) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]

    def test_helper_unit_is_the_measured_one(self):
        """The unit of spec 5.3 (measured as a whole, M3): every line is there, and no further capability."""
        spec = os.path.join(os.path.dirname(__file__), "..", "docs", "specs", "dgpu-standin-service.md")
        with open(spec) as f:
            block = f.read().split("### 5.3 Unit")[1].split("```")[1]
        unit = self.lines("surface-gpu-clockd.service")
        for line in block.strip().splitlines():
            self.assertIn(line, unit)
        self.assertEqual([x for x in unit if x.startswith(("CapabilityBoundingSet=", "AmbientCapabilities="))],
                         ["CapabilityBoundingSet=CAP_SYS_ADMIN"])

    def test_socket_is_for_root_only(self):
        unit = self.lines("surface-gpu-clockd.socket")
        for line in ("ListenStream=/run/surface-gpu-clock/sock", "SocketMode=0600", "SocketUser=root"):
            self.assertIn(line, unit)

    def test_daemon_unit_wants_the_socket_and_gets_no_new_capability(self):
        unit = self.lines("surface-powerd.service")
        wanted = [word for line in unit if line.startswith("Wants=") for word in line.split("=", 1)[1].split()]
        self.assertIn("surface-gpu-clockd.socket", wanted)
        self.assertIn("CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SYS_RAWIO", unit)


if __name__ == "__main__":
    unittest.main()
