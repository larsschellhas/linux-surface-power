"""dGPU stand-in: regulate the measured dGPU power to the blob's dGPU limit (output 201) through a graphics clock cap.

Spec outputs 6, deviation DO-1 (opt-in). Windows sets a dGPU power limit; the SB3's NVIDIA driver on Linux refuses one
(`nvidia-smi -pl`), but accepts a clock cap. Every NVML session opens the GPU and restarts its idle timer, so this
module only talks to NVML while another process already has an NVIDIA device open and so keeps the GPU awake.
"""
from __future__ import annotations

import ctypes
import glob
import os
from dataclasses import dataclass
from typing import Callable

STEP_MHZ = 15             # clock granularity of the GTX 1660 Ti (supported clocks in 15 MHz steps) [M]
GAIN_MHZ_PER_W = 20.0     # ~10 MHz/W measured between 900 and 1300 MHz, doubled for a fast first approach [M]
BAND_ABOVE_W = 0.5        # lower the cap above target + 0.5 W
BAND_BELOW_W = 2.0        # raise the cap below target - 2 W
AT_CAP_MHZ = 30           # "running at the cap": clock >= cap - 30 MHz
SAMPLE_MS = 2000
# A client is any other process with an NVIDIA device node open. `power/runtime_usage` is not reliable for this:
# it read 2 with a CUDA client once, but 1 with a client another time [M].


class NvmlError(Exception):
    pass


@dataclass(frozen=True)
class Sample:
    power_w: float
    clock_mhz: int
    default_limit_w: float
    min_clock_mhz: int
    max_clock_mhz: int


def _round_step(mhz: float) -> int:
    return int(round(mhz / STEP_MHZ)) * STEP_MHZ


def next_cap(cap: int | None, power: float, target: float, clock: int, lo: int, hi: int) -> int | None:
    """One step of the control law (spec outputs 6). Returns the new cap in MHz, or None for "no cap"."""
    ref = cap if cap is not None else hi
    if power > target + BAND_ABOVE_W:
        base = min(ref, clock)                      # start from where the GPU actually runs
        new = _round_step(base - GAIN_MHZ_PER_W * (power - target))
        new = min(new, base - STEP_MHZ) if cap is not None else new
        return max(lo, new)
    if cap is not None and power < target - BAND_BELOW_W and clock >= cap - AT_CAP_MHZ:
        new = cap + STEP_MHZ
        return None if new >= hi else new
    return cap


class Nvml:
    """Minimal NVML session through ctypes (standard library only). Use as a context manager: one session per sample."""

    def __init__(self, pci: str):
        self.pci = pci
        self.lib = None
        self.dev = ctypes.c_void_p()

    def _call(self, name: str, *args) -> None:
        rc = getattr(self.lib, name)(*args)
        if rc != 0:
            raise NvmlError(f"{name} returned {rc}")

    def __enter__(self) -> "Nvml":
        try:
            self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError as e:
            raise NvmlError(f"libnvidia-ml.so.1 not loadable: {e}") from None
        self._call("nvmlInit_v2")
        try:
            self._call("nvmlDeviceGetHandleByPciBusId_v2", self.pci.encode(), ctypes.byref(self.dev))
        except NvmlError:
            self.lib.nvmlShutdown()
            raise
        return self

    def __exit__(self, *exc) -> bool:
        if self.lib is not None:
            self.lib.nvmlShutdown()
        return False

    def _uint(self, name: str, *args) -> int:
        v = ctypes.c_uint()
        self._call(name, self.dev, *args, ctypes.byref(v))
        return v.value

    def read(self) -> Sample:
        graphics = 0                                # NVML_CLOCK_GRAPHICS
        return Sample(power_w=self._uint("nvmlDeviceGetPowerUsage") / 1000,
                      clock_mhz=self._uint("nvmlDeviceGetClockInfo", graphics),
                      default_limit_w=self._uint("nvmlDeviceGetPowerManagementDefaultLimit") / 1000,
                      min_clock_mhz=300,            # lowest supported graphics clock on the SB3 [M]
                      max_clock_mhz=self._uint("nvmlDeviceGetMaxClockInfo", graphics))

    def temperature_c(self) -> int:
        return self._uint("nvmlDeviceGetTemperature", ctypes.c_int(0))   # NVML_TEMPERATURE_GPU

    def set_cap(self, max_mhz: int) -> None:
        self._call("nvmlDeviceSetGpuLockedClocks", self.dev, ctypes.c_uint(300), ctypes.c_uint(max_mhz))

    def reset_cap(self) -> None:
        self._call("nvmlDeviceResetGpuLockedClocks", self.dev)


def find_nvidia_gpu(root: str = "/") -> str | None:
    """PCI address of the first NVIDIA display controller (vendor 0x10de, class 0x03xxxx)."""
    for d in sorted(glob.glob(os.path.join(root, "sys/bus/pci/devices/*"))):
        try:
            with open(os.path.join(d, "vendor")) as f:
                vendor = f.read().strip()
            with open(os.path.join(d, "class")) as f:
                cls = f.read().strip()
        except OSError:
            continue
        if vendor == "0x10de" and cls.startswith("0x03"):
            return os.path.basename(d)
    return None


def other_nvidia_client(root: str = "/") -> bool:
    """True if a process other than this one has /dev/nvidia* open (needs root to see all processes).

    Plain scandir/readlink with an early exit: the glob version cost ~70 ms per scan on the SB3 [M]."""
    own = str(os.getpid())
    proc = os.path.join(root, "proc")
    try:
        pids = [e.name for e in os.scandir(proc) if e.name.isdigit() and e.name != own]
    except OSError:
        return False
    for pid in pids:
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            with os.scandir(fd_dir) as it:
                for e in it:
                    try:
                        if os.readlink(e.path).startswith("/dev/nvidia"):
                            return True
                    except OSError:
                        continue                    # the descriptor went away
        except OSError:
            continue                                # the process went away, or no permission
    return False


PROBE_MS = 10000          # rescan for other NVIDIA clients at most every 10 s; a scan of /proc/*/fd costs a few ms CPU [M]


def runtime_status(root: str, pci: str) -> str | None:
    try:
        with open(os.path.join(root, "sys/bus/pci/devices", pci, "power/runtime_status")) as f:
            return f.read().strip()
    except OSError:
        return None


class ClientProbe:
    """Whether another process keeps the NVIDIA GPU awake, shared by the stand-in and the dGPU sensors.

    A runtime-suspended GPU has no client that matters: then there is no scan at all. Otherwise the result of a scan of
    /proc/*/fd is kept for PROBE_MS; when the GPU wakes up, it is scanned at once. A client that opens an already awake GPU is
    seen within 10 s."""

    def __init__(self, root: str, pci: str | None, interval_ms: int = PROBE_MS):
        self.root, self.pci, self.interval_ms = root, pci, interval_ms
        self.was_suspended = True
        self.checked_ms: int | None = None
        self.result = False

    def has_client(self, now_ms: int) -> bool:
        if self.pci is None:
            return False
        if runtime_status(self.root, self.pci) == "suspended":
            self.was_suspended = True
            return False
        # The GPU just woke up: usually because a client opened it, so look at once instead of after the interval.
        woke = self.was_suspended
        self.was_suspended = False
        if woke or self.checked_ms is None or now_ms - self.checked_ms >= self.interval_ms:
            self.result = other_nvidia_client(self.root)
            self.checked_ms = now_ms
        return self.result


class ClockCapStandIn:
    """Regulates the dGPU power to the target with a clock cap, sampling only while another client keeps the GPU awake."""

    def __init__(self, root: str = "/", nvml_factory: Callable[[str], object] = Nvml, dry_run: bool = False,
                 probe: ClientProbe | None = None):
        self.root = root
        self.factory = nvml_factory
        self.dry_run = dry_run
        self.pci = find_nvidia_gpu(root)
        self.probe = probe or ClientProbe(root, self.pci)
        self.enabled = self.pci is not None
        self.cap: int | None = None           # what is set on the GPU (None: no cap)
        self.would_cap: int | None = None     # dry-run: what would be set
        self.next_ms = 0
        self.last: Sample | None = None
        self.target: float | None = None
        self.error: str | None = None if self.pci else "no NVIDIA GPU found"
        self.session = None                   # open NVML session while another client keeps the GPU awake

    def _open(self):
        """NVML init costs ~20 ms CPU [M]: keep one session while a client keeps the GPU awake anyway."""
        if self.session is None:
            self.session = self.factory(self.pci).__enter__()
        return self.session

    def close(self) -> None:
        """End the NVML session, so that the GPU can go to sleep (no client, stop, sleep)."""
        if self.session is not None:
            try:
                self.session.__exit__(None, None, None)
            except NvmlError:
                pass
            self.session = None


    def _fail(self, e: Exception) -> None:
        self.enabled = False
        self.error = str(e)

    def step(self, now_ms: int, target_w: float | None) -> None:
        """Called every daemon cycle with the blob's dGPU limit in W (None: invalid -> release)."""
        if not self.enabled:
            return
        self.target = target_w
        if target_w is None:
            self.release()
            return
        if now_ms < self.next_ms:
            return
        if not self.probe.has_client(now_ms):
            self.close()                            # never keep the GPU awake; a set cap stays in force across D3cold
            return
        self.next_ms = now_ms + SAMPLE_MS
        try:
            nv = self._open()
            s = nv.read()
            self.last = s
            current = self.would_cap if self.dry_run else self.cap
            new = None if target_w >= s.default_limit_w else next_cap(
                current, s.power_w, target_w, s.clock_mhz, s.min_clock_mhz, s.max_clock_mhz)
            if self.dry_run:
                self.would_cap = new
            elif new != self.cap:
                nv.reset_cap() if new is None else nv.set_cap(new)
                self.cap = new
        except NvmlError as e:
            self.close()
            self._fail(e)

    def release(self) -> None:
        """Remove the cap (daemon stop, emergency, yield, sleep, invalid target)."""
        self.would_cap = None
        if self.cap is None or not self.enabled:
            self.close()
            return
        try:
            self._open().reset_cap()
            self.cap = None
        except NvmlError as e:
            self._fail(e)
        finally:
            self.close()

    def status(self) -> dict:
        s = self.last
        return {"enabled": self.enabled, "dry_run": self.dry_run, "target_w": self.target, "cap_mhz": self.cap,
                "would_cap_mhz": self.would_cap, "power_w": s.power_w if s else None,
                "clock_mhz": s.clock_mhz if s else None, "error": self.error}
