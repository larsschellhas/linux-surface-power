"""dGPU stand-in: regulate the measured dGPU power to the blob's dGPU limit (output 201) through a graphics clock cap.

Spec outputs 6, deviation DO-1 (opt-in). Windows sets a dGPU power limit; the SB3's NVIDIA driver on Linux refuses one
(`nvidia-smi -pl`), but accepts a clock cap. Every NVML query restarts the GPU's idle timer [M], so all of them go through
one idle gate (GpuGate, spec dgpu-standin-service 4): none while the GPU sleeps, and a pause once it is idle. The daemon
does not look for other processes: under its unit it cannot see them [M, B15]. Setting a cap needs CAP_SYS_ADMIN, which
the daemon's unit does not have [M]: the daemon sends the cap to the clock helper surface-gpu-clockd (spec 5, `setter`).
Without a setter the stand-in sets it itself through NVML (plain root only).

Measured on the SB3's GTX 1660 Ti only. The module itself is not tied to it (it takes the first NVIDIA display controller and
asks NVML for the limits), but the constants below are SB3 numbers. Whether another GPU accepts a power limit (`probe()`,
`scripts/dgpu-probe.py`) or this cap, and how many MHz a watt is worth there, is not known for the Laptop Studio GPUs
(`docs/research/14-laptop-studio-dgpu.md`).
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
# Idle gate (spec dgpu-standin-service 4). Without a further query the GPU suspends within 21.5 s [M, M1]; sampling every
# 2 s keeps it awake [M, M2].
QUIET_MS = 35000
IDLE_SAMPLES = 5          # [H, M5]
IDLE_UTIL = 1             # percent [H, M5]
IDLE_MARGIN_W = 2.0
RELOOKUP_MS = 5000
# Errors: permanent at once for these NVML codes, otherwise after MAX_FAILURES in a row (spec 6).
NVML_NOT_SUPPORTED, NVML_NO_PERMISSION = 3, 4
MAX_FAILURES = 5
NO_EFFECT_W, NO_EFFECT_MS = 5.0, 30000      # spec 7


class NvmlError(Exception):
    def __init__(self, text: str, code: int | None = None, permanent: bool = False):
        super().__init__(text)
        self.code = code
        self.permanent = permanent or code in (NVML_NOT_SUPPORTED, NVML_NO_PERMISSION)


@dataclass(frozen=True)
class Sample:
    power_w: float
    clock_mhz: int
    default_limit_w: float
    min_clock_mhz: int
    max_clock_mhz: int
    util_pct: int | None = None       # None: the driver did not answer


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


@dataclass(frozen=True)
class Capabilities:
    """What NVML reports about one GPU (all read-only). None: the driver did not answer that question."""

    name: str
    power_w: float | None
    default_limit_w: float | None
    enforced_limit_w: float | None
    limit_min_w: float | None            # range the driver would accept for a new power limit
    limit_max_w: float | None
    max_clock_mhz: int | None


def assess(c: Capabilities) -> list[str]:
    """Plain-language findings for an owner (spec outputs 6.1). Pure function of what the driver reported [H for the verdicts]."""
    out = []
    if c.limit_min_w is not None and c.limit_max_w is not None:
        if c.limit_min_w < c.limit_max_w:
            out.append(f"NVML reports a power-limit range of {c.limit_min_w:g} to {c.limit_max_w:g} W. That is no proof that a "
                       "limit can be set: the SB3 reports 1 to 65 W and still refuses every write [M]. "
                       "`scripts/dgpu-probe.py --try-limit` shows whether a write is accepted; only then is a power limit "
                       "a closer copy of the Windows limit than the clock cap.")
        else:
            out.append(f"NVML reports a fixed power limit of {c.limit_max_w:g} W (minimum equals maximum): no power limit can "
                       "be set; only the clock cap of this stand-in is left.")
    else:
        out.append("NVML does not report a power-limit range: no power limit can be set; only the clock cap is left.")
    if c.default_limit_w is None:
        out.append("The default power limit is unknown: the stand-in cannot tell when a target is above it, and will not run.")
    if c.max_clock_mhz is None:
        out.append("The maximum graphics clock is unknown: the stand-in cannot regulate and will not run.")
    return out


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
            raise NvmlError(f"{name} returned {rc}", rc)

    def __enter__(self) -> "Nvml":
        try:
            self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError as e:
            raise NvmlError(f"libnvidia-ml.so.1 not loadable: {e}", permanent=True) from None
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
                      max_clock_mhz=self._uint("nvmlDeviceGetMaxClockInfo", graphics),
                      util_pct=self._utilization())

    def _utilization(self) -> int | None:
        u = _Utilization()
        try:
            self._call("nvmlDeviceGetUtilizationRates", self.dev, ctypes.byref(u))
        except NvmlError:
            return None
        return u.gpu

    def temperature_c(self) -> int:
        return self._uint("nvmlDeviceGetTemperature", ctypes.c_int(0))   # NVML_TEMPERATURE_GPU

    def capabilities(self) -> "Capabilities":
        """Read-only questions to the driver: what it reports about the power limit and the clocks. Writes nothing."""
        name = ctypes.create_string_buffer(96)
        self._call("nvmlDeviceGetName", self.dev, name, ctypes.c_uint(96))

        def mw(fn_name):
            try:
                return self._uint(fn_name) / 1000
            except NvmlError:
                return None
        lo = hi = None
        try:
            a, b = ctypes.c_uint(), ctypes.c_uint()
            self._call("nvmlDeviceGetPowerManagementLimitConstraints", self.dev, ctypes.byref(a), ctypes.byref(b))
            lo, hi = a.value / 1000, b.value / 1000
        except NvmlError:
            pass
        try:
            clock = self._uint("nvmlDeviceGetMaxClockInfo", 0)      # NVML_CLOCK_GRAPHICS
        except NvmlError:
            clock = None
        return Capabilities(name=name.value.decode(errors="replace"), power_w=mw("nvmlDeviceGetPowerUsage"),
                            default_limit_w=mw("nvmlDeviceGetPowerManagementDefaultLimit"),
                            enforced_limit_w=mw("nvmlDeviceGetEnforcedPowerLimit"),
                            limit_min_w=lo, limit_max_w=hi, max_clock_mhz=clock)

    def max_clock_mhz(self) -> int:
        return self._uint("nvmlDeviceGetMaxClockInfo", 0)          # NVML_CLOCK_GRAPHICS

    def set_power_limit_mw(self, mw: int) -> None:
        self._call("nvmlDeviceSetPowerManagementLimit", self.dev, ctypes.c_uint(mw))

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


def runtime_status(root: str, pci: str) -> str | None:
    try:
        with open(os.path.join(root, "sys/bus/pci/devices", pci, "power/runtime_status")) as f:
            return f.read().strip()
    except OSError:
        return None


class GpuGate:
    """When NVML may be opened, shared by the stand-in and the dGPU sensors (spec dgpu-standin-service 4).

    `asleep`: the GPU is runtime-suspended; nothing is touched (even nvmlShutdown wakes it [M, M2b]).
    `sampling`: awake; one session, a sample every 2 s.
    `quiet`: idle for IDLE_SAMPLES samples; the session was closed while the GPU was awake, then QUIET_MS without any call.
    `gone`: no runtime status (the base with the GPU is detached).
    """

    def __init__(self, root: str, pci: str | None, factory: Callable[[str], object] = Nvml):
        self.root = root
        self.pci = pci
        self.factory = factory
        self.state = "asleep"
        self.session = None
        self.target_w: float | None = None    # the stand-in's target: a sample near or above it is never idle
        self.want_temp = False                # the dGPU sensors also read the temperature
        self.sample: Sample | None = None
        self.temp_c: int | None = None
        self.seq = 0                          # counts samples
        self.error: NvmlError | None = None
        self.error_seq = 0                    # counts failed samples
        self.failures = 0                     # in a row
        self.generation = 0                   # counts how often the device went away (a cap does not survive that)
        self.next_ms = 0
        self.quiet_until = 0
        self.idle = 0
        self.pause_due = False
        self.polled_ms: int | None = None
        self.lookup_ms = 0

    def open(self):
        """The session of the current sampling phase (NVML init costs ~20 ms CPU on an awake GPU [M])."""
        if self.session is None:
            self.session = self.factory(self.pci).__enter__()
        return self.session

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.__exit__(None, None, None)
            except NvmlError:
                pass
            self.session = None

    def reset(self) -> None:
        """After a system sleep: sample at once."""
        self.next_ms, self.quiet_until, self.idle, self.pause_due, self.polled_ms = 0, 0, 0, False, None
        if self.state == "quiet":
            self.state = "sampling"

    def poll(self, now_ms: int) -> str:
        """Called every daemon cycle by each consumer; a second call with the same time does nothing."""
        if self.pci is None or now_ms == self.polled_ms:
            return self.state
        self.polled_ms = now_ms
        status = runtime_status(self.root, self.pci)
        if status is None:
            self._gone(now_ms)
            return self.state
        if status == "suspended":
            # An open session is left alone: the GPU sleeps with it, and closing it would wake the GPU.
            self.state, self.idle, self.pause_due = "asleep", 0, False
            return self.state
        if self.state in ("asleep", "gone"):
            self.state, self.idle, self.next_ms = "sampling", 0, now_ms
        elif self.state == "quiet":
            if now_ms < self.quiet_until:
                return self.state
            self.state, self.idle, self.next_ms = "sampling", 0, now_ms
        elif self.pause_due:
            # One cycle after the last idle sample, so that its consumers could still use the session; the GPU is still awake.
            self.close()
            self.state, self.pause_due = "quiet", False
            return self.state
        if now_ms >= self.next_ms:
            self._take(now_ms)
        return self.state

    def _gone(self, now_ms: int) -> None:
        if self.state != "gone":
            self.state, self.idle, self.pause_due = "gone", 0, False
            self.close()
            self.sample, self.temp_c = None, None
            self.generation += 1
        if now_ms >= self.lookup_ms:
            self.lookup_ms = now_ms + RELOOKUP_MS
            self.pci = find_nvidia_gpu(self.root) or self.pci

    def _take(self, now_ms: int) -> None:
        self.next_ms = now_ms + SAMPLE_MS
        try:
            nv = self.open()
            sample = nv.read()
        except NvmlError as e:
            self.close()
            self.error = e
            self.error_seq += 1
            self.failures += 1
            self.sample, self.temp_c = None, None
            return
        temp = None
        if self.want_temp:
            try:
                temp = nv.temperature_c()
            except NvmlError:
                pass
        self.sample, self.temp_c, self.error, self.failures = sample, temp, None, 0
        self.seq += 1
        idle = (sample.util_pct is not None and sample.util_pct <= IDLE_UTIL
                and (self.target_w is None or sample.power_w < self.target_w - IDLE_MARGIN_W))
        self.idle = self.idle + 1 if idle else 0
        if self.idle >= IDLE_SAMPLES:
            self.pause_due = True
            self.quiet_until = now_ms + QUIET_MS


class ClockCapStandIn:
    """Regulates the dGPU power to the target with a clock cap, sampling through the idle gate."""

    def __init__(self, root: str = "/", nvml_factory: Callable[[str], object] = Nvml, dry_run: bool = False,
                 gate: GpuGate | None = None, setter=None, marker: str | None = None):
        self.root = root
        self.setter = setter                  # the clock helper (set_cap, reset_cap); None: NVML in this process
        self.marker = marker                  # file that exists while a cap may be set, for surface-power-reset
        self.helper_state: str | None = None
        self.dry_run = dry_run
        self.pci = gate.pci if gate else find_nvidia_gpu(root)
        self.gate = gate or GpuGate(root, self.pci, nvml_factory)
        if gate is not None:
            gate.factory = nvml_factory
        self.enabled = self.pci is not None
        self.cap: int | None = None           # what is set on the GPU (None: no cap)
        self.would_cap: int | None = None     # dry-run: what would be set
        self.last: Sample | None = None
        self.target: float | None = None
        self.error: str | None = None if self.pci else "no NVIDIA GPU found"
        self.events: list[tuple[str, str]] = []
        self.seen_seq = self.gate.seq
        self.seen_error = self.gate.error_seq
        self.seen_generation = self.gate.generation
        self.set_failures = 0
        self.resend = False                   # after a system sleep: send the cap (or its removal) again
        self.announced = False
        self.over_since: int | None = None
        self.warned_no_effect = False

    @property
    def factory(self):
        return self.gate.factory

    @factory.setter
    def factory(self, value) -> None:
        self.gate.factory = value

    @property
    def session(self):
        return self.gate.session

    def close(self) -> None:
        """End the NVML session (stop, sleep, invalid target)."""
        self.gate.close()

    def drain_events(self) -> list[tuple[str, str]]:
        out, self.events = self.events, []
        return out

    def _marked(self) -> bool:
        return self.marker is not None and os.path.exists(self.marker)

    def _unmark(self) -> None:
        if self._marked():
            try:
                os.remove(self.marker)
            except OSError:
                pass

    def _apply(self, cap: int | None) -> None:
        """Set (MHz) or remove (None) the cap. The marker is written before a cap and removed after a successful reset."""
        if cap is not None and self.marker is not None:
            try:
                with open(self.marker, "w") as f:
                    f.write(f"{cap}\n")
            except OSError as e:
                raise NvmlError(f"cannot write the cap marker {self.marker} ({e.strerror or e})", permanent=True) from None
        target = self.setter if self.setter is not None else self.gate.open()
        try:
            target.reset_cap() if cap is None else target.set_cap(cap)
        except NvmlError as e:
            if self.setter is not None:
                self.helper_state = getattr(e, "short", str(e))
            else:
                self.gate.close()
            raise
        if self.setter is not None:
            self.helper_state = "ok"
        if cap is None:
            self._unmark()

    def check_helper(self) -> None:
        """At the start in control mode (spec 6): without a helper the stand-in is off; a cap a predecessor left is removed."""
        if self.setter is None or not self.enabled:
            return
        try:
            self.setter.ping()                # never wakes the GPU
            self.helper_state = "ok"
            if self._marked():
                self._apply(None)
        except NvmlError as e:
            self.helper_state = getattr(e, "short", str(e))
            self._disable(str(e))

    def _disable(self, text: str) -> None:
        # With a helper, the reason to give up may lie in the reading: try once to take the cap back.
        if self.setter is not None and not self.dry_run and self.cap is not None:
            try:
                self._apply(None)
                self.cap = None
            except NvmlError:
                pass
        self.enabled = False
        self.error = text
        self.gate.close()
        self.gate.target_w = None
        state = (f"a clock cap of {self.cap} MHz may still be set" if self.cap is not None else
                 "the dGPU limit does not reach the GPU, which runs at its driver's limit")
        self.events.append(("warning", f"dGPU stand-in off for this run: {text}; {state}"))

    def step(self, now_ms: int, target_w: float | None) -> None:
        """Called every daemon cycle with the blob's dGPU limit in W (None: invalid -> release)."""
        if not self.enabled:
            return
        self.target = target_w
        if target_w is None:
            self.release()
            return
        self.gate.target_w = target_w
        state = self.gate.poll(now_ms)
        if self.gate.generation != self.seen_generation:
            self.seen_generation = self.gate.generation
            self.cap = self.would_cap = None                   # a re-added device is uncapped [H, M4]
            self.set_failures = 0
            self._unmark()
        if state != "sampling":
            self.over_since = None
        if self.gate.error_seq != self.seen_error:
            self.seen_error = self.gate.error_seq
            if self.gate.error is not None and (self.gate.error.permanent or self.gate.failures >= MAX_FAILURES):
                self._disable(str(self.gate.error))
            return
        if self.gate.seq == self.seen_seq or self.gate.sample is None:
            return
        self.seen_seq = self.gate.seq
        s = self.last = self.gate.sample
        current = self.would_cap if self.dry_run else self.cap
        new = None if target_w >= s.default_limit_w else next_cap(
            current, s.power_w, target_w, s.clock_mhz, s.min_clock_mhz, s.max_clock_mhz)
        self._watch_effect(now_ms, s, target_w)
        if self.dry_run:
            self.would_cap = new
            return
        if new == self.cap and not self.resend:
            return
        try:
            self._apply(new)
        except NvmlError as e:
            self.set_failures += 1
            if e.permanent or self.set_failures >= MAX_FAILURES:
                self._disable(str(e))
            return
        self.set_failures = 0
        self.resend = False
        self.cap = new
        if new is not None and not self.announced:
            self.announced = True
            self.events.append(("info", f"dGPU stand-in: first clock cap set: {new} MHz (target {target_w:g} W)"))

    def _watch_effect(self, now_ms: int, s: Sample, target_w: float) -> None:
        if self.dry_run or self.cap is None or s.power_w <= target_w + NO_EFFECT_W:
            self.over_since = None
            return
        if self.over_since is None:
            self.over_since = now_ms
        elif now_ms - self.over_since >= NO_EFFECT_MS and not self.warned_no_effect:
            self.warned_no_effect = True
            self.events.append(("warning", f"dGPU stand-in: the clock cap has no effect: {s.power_w:g} W for "
                                           f"{NO_EFFECT_MS // 1000} s against a target of {target_w:g} W (cap {self.cap} MHz)"))

    def after_sleep(self) -> None:
        """The daemon does not touch the GPU around a system sleep (spec dgpu-standin-service 6): the cap stays as it is,
        and the next sample sends it again, because it is not known whether it survived [H]."""
        self.resend = self.cap is not None or self._marked()
        self.over_since = None
        self.set_failures = 0
        self.gate.failures = 0
        self.gate.reset()

    def release(self) -> None:
        """Remove the cap (daemon stop, emergency, yield, invalid target). A failure that may pass is tried again at the
        next call; the cap stays remembered and marked until it is gone."""
        self.would_cap = None
        self.over_since = None
        self.gate.target_w = None
        if not self.enabled or self.dry_run or (self.cap is None and not self._marked()):
            self.gate.close()
            return
        try:
            self._apply(None)
            self.cap = None
            self.set_failures, self.resend = 0, False
        except NvmlError as e:
            self.set_failures += 1
            if e.permanent or self.set_failures >= MAX_FAILURES:
                self._disable(str(e))
            elif self.set_failures == 1:
                what = f"clock cap of {self.cap} MHz" if self.cap is not None else "clock cap"
                self.events.append(("warning", f"dGPU stand-in: {what} not removed yet ({e}); it is tried again"))
        finally:
            self.gate.close()

    def status(self) -> dict:
        s = self.last
        reason = f"off: {self.error}" if not self.enabled else "dry-run" if self.dry_run else None
        return {"enabled": self.enabled, "dry_run": self.dry_run, "target_w": self.target, "cap_mhz": self.cap,
                "would_cap_mhz": self.would_cap, "power_w": s.power_w if s else None,
                "clock_mhz": s.clock_mhz if s else None, "error": self.error,
                "gate": self.gate.state, "acting": reason is None, "reason": reason, "helper": self.helper_state}
