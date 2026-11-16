"""Fan and charging outputs `B620798F` (spec outputs 9): one SAM request per channel, sent through the SSAM debug device.

The commands, units and send rules are in docs/research/11-fan-charging.md. In short:
  - a channel becomes one write request (no response) with a 1-3 byte payload: u8, u16 little endian or float32 little endian;
  - the value is clamped to the blob range, and a new request goes out only if it moved by at least the dead band (100 rpm for
    101-103, 1 otherwise), at most once per 5 s per channel, and on the first cycle after start or resume;
  - a failed request is tried again after the rate limit; three failures in a row make the channel log-only;
  - the EC's values cannot be read back, so restore = send the neutral frame (0) for every channel that was sent. The frame is
    recorded in the snapshot before the first send of the channel (supervisor.py).
The fan profile (201..203) and the fan state (301) are NOT sent here: the linux-surface kernel sends them (surface_platform_profile,
suspend hooks). 301 = 2 (standby) is never sent by the daemon. The temperature thresholds (302-311) are off unless a device
profile opts in (`fan_thresholds = true`).
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from ..loader.model import ChannelRef, OutputChannel
from .inputs import SSAM_CDEV, CdevTransport

FAN_GUID = "B620798F-6FB0-42EF-B9B3-D2370402E089"
TC_TMP, TC_FAN = 0x03, 0x05
TC_NAMES = {TC_FAN: "FAN", TC_TMP: "TMP"}
RATE_MS = 5000          # at most one request per channel per 5 s (the Windows evaluation instance)
MAX_FAILS = 3           # then the channel is log-only
SLEEP_STATE = 2         # fan state 2 = standby: never sent by the daemon

# Fan profile numbers that surface_platform_profile sends for the platform profile (11-fan-charging.md 5.1).
PROFILE_VALUES = {"low-power": 1, "quiet": 1, "cool": 1, "balanced": 2, "balanced-performance": 3, "performance": 4}


@dataclass(frozen=True)
class Frame:
    """One SAM request. `wire()` is the layout of 11-fan-charging.md 3.1."""
    tc: int
    iid: int
    cid: int
    payload: bytes
    tid: int = 1
    flags: int = 0          # 0: sequenced write, no response

    def wire(self) -> bytes:
        return bytes([self.tc, self.tid, self.iid, self.flags, self.cid]) + struct.pack("<H", len(self.payload)) + self.payload

    def label(self) -> str:
        return f"{TC_NAMES.get(self.tc, self.tc)} 0x{self.cid:02x} iid {self.iid}"

    def to_dict(self) -> dict:
        return {"tc": self.tc, "tid": self.tid, "iid": self.iid, "cid": self.cid, "flags": self.flags,
                "payload": self.payload.hex()}

    @classmethod
    def from_dict(cls, d: dict) -> "Frame":
        return cls(int(d["tc"]), int(d["iid"]), int(d["cid"]), bytes.fromhex(d["payload"]), int(d.get("tid", 1)),
                   int(d.get("flags", 0)))


@dataclass(frozen=True)
class Spec:
    channel: int
    tc: int
    cid: int
    iid: int
    fmt: str                 # "u8", "u16" or "f32"
    hi: float                # hard upper bound of the value (on top of the blob range)
    dead: int = 1            # dead band: a new request only if the value moved by at least this much
    neutral: int | None = 0  # value of the restore frame; None: no neutral value is known
    route: str = "sam"       # "sam", "kernel-profile", "kernel-suspend", "optin" (SAM, off unless the profile opts in), "none"

    @property
    def tc_name(self) -> str:
        return TC_NAMES[self.tc]


def _build() -> dict[int, Spec]:
    s: dict[int, Spec] = {}
    for n in range(3):                                   # fan 1..3: the instance ID is the fan number
        s[101 + n] = Spec(101 + n, TC_FAN, 0x0B, 1 + n, "u16", 10000, dead=100)
        s[111 + n] = Spec(111 + n, TC_FAN, 0x0C, 1 + n, "f32", 100)
        s[114 + n] = Spec(114 + n, TC_FAN, 0x15, 1 + n, "u8", 255)
        s[121 + n] = Spec(121 + n, TC_FAN, 0x12, 1 + n, "f32", 100)
        s[124 + n] = Spec(124 + n, TC_FAN, 0x14, 1 + n, "u8", 255)
        # The kernel sends the profile for the first fan only [H, not checked for fan 2 and 3].
        s[201 + n] = Spec(201 + n, TC_FAN, 0x0E, 1 + n, "u8", 4, neutral=None, route="kernel-profile" if n == 0 else "none")
    s[301] = Spec(301, TC_FAN, 0x0F, 1, "u8", 2, neutral=None, route="kernel-suspend")
    s[306] = Spec(306, TC_FAN, 0x1C, 1, "f32", 100)
    s[312] = Spec(312, TC_FAN, 0x16, 1, "u8", 255)
    for ch, cid in ((302, 0x19), (304, 0x1A), (305, 0x1B), (307, 0x1D)):
        s[ch] = Spec(ch, TC_FAN, cid, 1, "u16", 65535, neutral=None, route="optin")
    for ch, cid in ((308, 0x25), (309, 0x26), (310, 0x27), (311, 0x28)):
        s[ch] = Spec(ch, TC_TMP, cid, 1, "u16", 65535, neutral=None, route="optin")
    return s


SPECS = _build()
OPT_IN = frozenset(c for c, sp in SPECS.items() if sp.route == "optin")


def payload(spec: Spec, value: float) -> bytes:
    """The payload for a value: u8, u16 LE or float32 LE (11-fan-charging.md 3.1)."""
    if spec.fmt == "u8":
        return bytes([int(min(max(round(value), 0), 255))])
    if spec.fmt == "u16":
        return struct.pack("<H", int(min(max(round(value), 0), 65535)))
    return struct.pack("<f", float(value))


def frame_for(spec: Spec, value: float) -> Frame:
    return Frame(spec.tc, spec.iid, spec.cid, payload(spec, value))


def neutral_frame(channel: int) -> Frame | None:
    sp = SPECS.get(channel)
    if sp is None or sp.neutral is None:
        return None
    return frame_for(sp, sp.neutral)


def send_frame(transport, frame: Frame) -> bool:
    try:
        return bool(transport.send(frame.tc, frame.cid, frame.iid, frame.payload, tid=frame.tid))
    except OSError:
        return False


class NullTransport:
    """No EC requests (`--no-ec`)."""
    path = ""

    def can_write(self) -> bool:
        return False

    def needs_root(self) -> bool:
        return False

    def send(self, tc, cid, iid, payload, tid=1) -> bool:
        return False

    def read(self, tc, cid, iid=1, tid=1):
        return None


# --- the kernel path for 201 and 301 ---

def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


@dataclass(frozen=True)
class KernelPath:
    bound: bool              # surface_platform_profile is bound to its SSAM device
    fan_seen: bool           # an hwmon `surface_fan` exists (evidence for `has_fan`; the property itself is not in sysfs) [H]


def kernel_path(root: str) -> KernelPath:
    drv = os.path.join(root, "sys/bus/surface_aggregator/drivers/surface_platform_profile")
    bound = False
    if os.path.isdir(drv):
        bound = any(os.path.islink(os.path.join(drv, e)) and e not in ("module",) for e in os.listdir(drv))
    if not bound:
        cls = os.path.join(root, "sys/class/platform-profile")
        if os.path.isdir(cls):
            bound = any((_read(os.path.join(cls, e, "name")) or "") == "surface_platform_profile" for e in os.listdir(cls))
    fan = False
    hw = os.path.join(root, "sys/class/hwmon")
    if os.path.isdir(hw):
        fan = any(_read(os.path.join(hw, e, "name")) == "surface_fan" for e in os.listdir(hw))
    return KernelPath(bound, fan)


def platform_profile(root: str) -> str | None:
    """The platform profile that surface_platform_profile sends the fan profile for."""
    cls = os.path.join(root, "sys/class/platform-profile")
    if os.path.isdir(cls):
        for e in sorted(os.listdir(cls)):
            if "surface" in (_read(os.path.join(cls, e, "name")) or ""):
                v = _read(os.path.join(cls, e, "profile"))
                if v:
                    return v
    return _read(os.path.join(root, "sys/firmware/acpi/platform_profile"))


# --- the stage ---

@dataclass
class _Chan:
    spec: Spec
    path: str                    # "sam", "needs-root", "kernel-profile", "kernel-suspend", "disabled", "none"
    sent: int | None = None      # last value that went out (None: send on the next cycle)
    last_try: int | None = None  # ms of the last attempt
    fails: int = 0
    off: bool = False
    would: Frame | None = None
    recorded: bool = False


@dataclass(frozen=True)
class FanResult:
    ref: ChannelRef
    kind: str            # "written", "pending", "dry-run", "kernel", "disabled", "log-only"
    value: int
    frame: Frame | None
    written: bool        # a request went out in this cycle


class FanStage:
    """The write path of the fan and charging outputs. Built for the channels the blob has."""

    def __init__(self, root: str, channels, ranges: dict[ChannelRef, OutputChannel] | None = None, transport=None,
                 dry_run: bool = False, thresholds: bool = False, on_touch=None):
        """on_touch(key, frame): called before the first send of a channel with the neutral frame to record for the restore;
        raising OSError stops the send (no restore frame, no write)."""
        self.root, self.ranges, self.dry_run = root, ranges or {}, dry_run
        self.thresholds, self.on_touch = thresholds, on_touch
        self.transport = transport if transport is not None else CdevTransport(os.path.join(root, SSAM_CDEV.lstrip("/")))
        self.notes: list[str] = []
        self.events: list[tuple[str, str]] = []
        self.kernel = kernel_path(root)
        self.chans: dict[ChannelRef, _Chan] = {}
        self._logged: set = set()
        can = bool(self.transport.can_write())
        needs_root = (not can) and bool(getattr(self.transport, "needs_root", lambda: False)())
        for ref in sorted(r for r in channels if r.guid == FAN_GUID):
            sp = SPECS.get(ref.channel)
            if sp is None:
                self.notes.append(f"{ref}: unknown fan or charging channel, log-only")
                continue
            if sp.route == "kernel-profile":
                path = "kernel-profile" if self.kernel.bound else "none"
            elif sp.route == "kernel-suspend":
                path = "kernel-suspend" if self.kernel.bound else "none"
            elif sp.route == "none":
                path = "none"
            elif sp.route == "optin" and not thresholds:
                path = "disabled"
            else:
                path = "sam" if can else "needs-root" if needs_root else "none"
            self.chans[ref] = _Chan(sp, path)
        if any(c.path == "disabled" for c in self.chans.values()):
            self.notes.append("fan and charging temperature thresholds (302-311) stay log-only: the device profile does not set "
                              "`fan_thresholds = true`")
        if any(c.path.startswith("kernel") for c in self.chans.values()):
            self.notes.append("fan profile and fan state are sent by the kernel (surface_platform_profile), not by the daemon"
                              + ("" if self.kernel.fan_seen else "; fan support (has_fan) is not detectable [H]"))
        if any(c.path == "sam" for c in self.chans.values()):
            self.notes.append("fan and charging outputs go through the SSAM debug device; the EC's values cannot be read back, "
                              "the restore sends neutral frames")
        if any(c.spec.route == "optin" and c.path == "sam" for c in self.chans.values()):
            self.notes.append("temperature thresholds are enabled by the profile; they have no known neutral value, only a "
                              "reboot undoes them")

    # --- identity ---
    def has_path(self, ref: ChannelRef, report: bool = False) -> bool:
        """True if this output has a Linux path. `report`: a tool run as a normal user counts the debug device as a path."""
        c = self.chans.get(ref)
        if c is None or c.off:
            return False
        return c.path in ("sam", "kernel-profile", "kernel-suspend") or (report and c.path == "needs-root")

    def describe(self, ref: ChannelRef) -> str:
        c = self.chans.get(ref)
        if c is None:
            return "no Linux path"
        if c.off:
            return "no Linux path (log-only after failed requests)"
        if c.path == "sam":
            return f"SAM {c.spec.tc_name} 0x{c.spec.cid:02x} (cdev)"
        if c.path == "needs-root":
            return f"SAM {c.spec.tc_name} 0x{c.spec.cid:02x} (cdev, needs root)"
        if c.path == "kernel-profile":
            return "kernel (platform profile)"
        if c.path == "kernel-suspend":
            return "kernel (suspend)"
        if c.path == "disabled":
            return "disabled (profile opt-in)"
        return "no Linux path"

    def sends(self, ref: ChannelRef) -> bool:
        """True if the daemon sends this channel itself (a SAM path, not the kernel's)."""
        c = self.chans.get(ref)
        return c is not None and c.path == "sam" and not c.off

    # --- apply ---
    def _value(self, ref: ChannelRef, spec: Spec, v: float) -> int:
        x = int(round(v))
        rng = self.ranges.get(ref)
        if rng is not None:
            x = int(min(max(x, round(rng.min)), round(rng.max)))
        return int(min(max(x, 0), spec.hi))

    def _event(self, prio: str, text: str, key=None) -> None:
        if key is not None:
            if key in self._logged:
                return
            self._logged.add(key)
        self.events.append((prio, text))

    def drain_events(self) -> list[tuple[str, str]]:
        out, self.events = self.events, []
        return out

    def apply(self, values: dict[ChannelRef, float], now_ms: int) -> dict[ChannelRef, FanResult]:
        out: dict[ChannelRef, FanResult] = {}
        for ref, v in values.items():
            c = self.chans.get(ref)
            if c is None:
                if ref.guid == FAN_GUID:
                    out[ref] = FanResult(ref, "log-only", int(round(v)), None, False)
                continue
            sp = c.spec
            x = self._value(ref, sp, v)
            if c.path in ("kernel-profile", "kernel-suspend"):
                self._check_kernel(ref, c, x)
                out[ref] = FanResult(ref, "kernel", x, None, False)
                continue
            if c.path == "disabled":
                out[ref] = FanResult(ref, "disabled", x, frame_for(sp, x), False)
                continue
            if c.path in ("none", "needs-root") or c.off:
                out[ref] = FanResult(ref, "log-only", x, None, False)
                continue
            frame = frame_for(sp, x)
            if self.dry_run:
                c.would = frame
                out[ref] = FanResult(ref, "dry-run", x, frame, False)
                continue
            if c.sent is not None and abs(x - c.sent) < sp.dead:
                out[ref] = FanResult(ref, "written", x, frame, False)
                continue
            if c.last_try is not None and now_ms - c.last_try < RATE_MS:
                out[ref] = FanResult(ref, "written" if c.sent is not None else "pending", x, frame, False)
                continue
            c.last_try = now_ms
            ok = self._send(ref, c, frame)
            if ok:
                c.sent, c.fails = x, 0
            else:
                c.fails += 1
                if c.fails >= MAX_FAILS:
                    c.off = True
                    self.notes.append(f"{ref}: {c.fails} requests failed, log-only from now on")
                    self._event("warning", f"fan output {ref} ({sp.tc_name} 0x{sp.cid:02x}): {c.fails} requests failed in a "
                                           "row, log-only from now on")
            out[ref] = FanResult(ref, "written" if ok else ("log-only" if c.off else "pending"), x, frame, ok)
        return out

    def _send(self, ref: ChannelRef, c: _Chan, frame: Frame) -> bool:
        """Record the neutral frame first (a crash after the send must find it), then send."""
        n = neutral_frame(c.spec.channel)
        if self.on_touch is not None and not c.recorded:
            try:
                self.on_touch(f"fan:{c.spec.channel}", n)
            except OSError as e:
                self._event("error", f"fan output {ref}: restore frame not recorded ({e}); nothing sent")
                return False
            c.recorded = True
        if n is None and not c.recorded:
            c.recorded = True
            self._event("warning", f"fan output {ref} has no known neutral value; only a reboot undoes it", key=("noneutral", ref))
        return send_frame(self.transport, frame)

    def _check_kernel(self, ref: ChannelRef, c: _Chan, x: int) -> None:
        if c.spec.channel == 201:
            prof = platform_profile(self.root)
            expect = PROFILE_VALUES.get(prof or "")
            if expect is not None and x != expect:
                self._event("info", f"fan profile: the blob asks for {x}, the kernel sends {expect} for the platform profile "
                                    f"{prof!r}; the daemon does not send it", key=("profile", x, expect))
        elif c.spec.channel == 301 and x == SLEEP_STATE:
            self._event("info", "fan state: the blob asks for 2 (standby); the daemon never sends it, the kernel does in its "
                                "suspend hook", key=("state", x))

    # --- restore ---
    def forget(self) -> None:
        """After a restore (or at resume): everything is sent again on the next cycle."""
        for c in self.chans.values():
            c.sent, c.last_try, c.would, c.fails = None, None, None, 0
            c.recorded = False

    def touched(self) -> list[ChannelRef]:
        return [r for r, c in self.chans.items() if c.sent is not None or c.last_try is not None]

    def status(self) -> list[dict]:
        out = []
        for ref, c in sorted(self.chans.items()):
            f = c.would
            out.append({"channel": str(ref), "path": self.describe(ref), "sent": c.sent, "failures": c.fails,
                        "would": f.wire().hex() if f else None})
        return out
