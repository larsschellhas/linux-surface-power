"""Output stage (spec outputs.md): the single write path for limits (NFR-2).

Order for every value: clamp to the blob's output range, to the hard limits and to the user's narrowing; convert mW -> µW;
write only on a change; read back. In dry-run nothing is written.
The powercap `max_power_uw` is not used as a bound: on the SB3 it reports the 15 W TDP while the firmware PL1 is 30 W [M].
"""
import glob
import os
from dataclasses import dataclass

from ..loader.model import ChannelRef, OutputChannel
from . import msr, rapl_copies

LIMITS = "0B7B5350-BB8C-4459-8D4A-A75C57073801"

# Hard limits in W (spec outputs 2). A configuration can only narrow them.
HARD = {"pl1": (3, 120), "pl2": (3, 200), "pl4": (5, 250), "psys1": (5, 250), "psys2": (5, 250), "pl3": (5, 250)}

# Channel -> (target name, powercap zone name, constraint name). PL3 is handled separately (MSR).
CHANNELS = {
    51: ("pl1", "package-0", "long_term"),
    52: ("pl2", "package-0", "short_term"),
    53: ("pl4", "package-0", "peak_power"),
    54: ("psys1", "psys", "long_term"),
    55: ("psys2", "psys", "short_term"),
}
PL3_CHANNEL = 59
# PL1 time window (`CF2F6076/61`, the Windows SoC client's "average time", docs/research/12 2.1): ms in the blob, µs in powercap.
SOC = "CF2F6076-EC8B-4D3D-AF61-CC76B118588A"
WINDOW_CHANNEL = 61
# The Windows client rounds the value UP to the next window the register can hold: 4 steps per octave from 2^10 to 2^16 units of
# 1/1024 s (1 s ... 112 s). Thresholds of its table [C]; the unit 1/1024 s is the register's default time unit [H].
# The Windows client compares the millisecond value with the unit counts themselves (1024, 1280, ... 114688), i.e. it takes 1 ms
# as one unit [C]; the written window is then the unit count x 1/1024 s. WINDOW_UNITS are those thresholds.
WINDOW_UNITS = [round(2 ** y * (1 + f / 4)) for y in range(10, 17) for f in range(4)]
WINDOW_STEPS_US = [round(u * 1_000_000 / 1024) for u in WINDOW_UNITS]
WINDOW_HARD_US = (WINDOW_STEPS_US[0], WINDOW_STEPS_US[-1])      # 1.0 s ... 112 s
# On the mmio path these go to the MMIO zone; PL4 and psys stay on their MSR zones (spec kernel-mmio-daemon 2.3).
MMIO_NAMES = ("pl1", "pl2")
# Hardware rounding tolerance for foreign-change detection (spec supervisor 5).
TOLERANCE_UW = 200_000


class PathFailure(Exception):
    """The mmio path cannot go on (spec kernel-mmio-daemon 4.1, 2.6): the daemon restores its snapshot and exits with an error.
    `marker`: create /run/surface-power/mmio-unusable, so that the restart chooses msr for the rest of the boot (not when the
    zone is simply gone: a restart then chooses again)."""

    def __init__(self, reason: str, marker: bool = True):
        super().__init__(reason)
        self.marker = marker


def _write(path: str, text: str) -> None:
    """Every write of the stage goes through here (tests record the order)."""
    with open(path, "w") as f:
        f.write(text)


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


@dataclass
class Target:
    """A powercap constraint file."""
    name: str
    path: str
    hard_min_uw: int
    hard_max_uw: int
    last_uw: int | None = None     # last value we wrote, as read back
    wrote_uw: int | None = None    # last value we wrote, as requested (a read-back right after the write may lag behind it)

    def shape(self, uw: int) -> int:
        """Last step before writing: a target may round the value the way the Windows client does."""
        return uw

    def tolerance(self, uw: int) -> int:
        """How far a read-back value may differ from the written one (hardware rounding)."""
        return TOLERANCE_UW

    def changed(self, uw: int) -> bool:
        """Is `uw` a new value compared with the last one written (write on change only, spec outputs 1)?"""
        return self.last_uw is None or abs(self.last_uw - uw) > self.tolerance(uw)

    @property
    def snapshot_key(self) -> str:
        return self.path

    def read_uw(self) -> int | None:
        v = _read(self.path)
        return int(v) if v and v.isdigit() else None

    def write_uw(self, uw: int) -> None:
        _write(self.path, str(uw))


@dataclass
class WindowTarget(Target):
    """The PL1 time window (`constraint_0_time_window_us`, spec outputs 3.2). The value is in µs here, so it shares the µ-unit code
    path with the power limits. The register holds only 4 steps per octave, so a read-back differs from the request by up to ~14 %."""

    @property
    def snapshot_key(self) -> str:
        return self.path

    def shape(self, uw: int) -> int:
        ms = uw / 1000
        for units, step in zip(WINDOW_UNITS, WINDOW_STEPS_US):
            if ms <= units:
                return step
        return WINDOW_STEPS_US[-1]

    def tolerance(self, uw: int) -> int:
        return max(TOLERANCE_UW, int(uw * 0.15))

    def changed(self, uw: int) -> bool:
        """A new step is a change even when it is within the read-back tolerance of the last one (28 s -> 32 s is 14 %)."""
        return self.wrote_uw != uw


@dataclass
class MsrTarget(Target):
    """PL3 in MSR 0x615 (spec outputs 4): only the limit field changes; time window and enable bit stay as read."""
    root: str = "/"
    unit_w: float = 0.125

    @property
    def snapshot_key(self) -> str:
        return msr.key(self.root, msr.PL3_CONTROL)

    def read_uw(self) -> int | None:
        v = msr.read(self.root, msr.PL3_CONTROL)
        return None if v is None else int(round((v & msr.PL3_LIMIT_MASK) * self.unit_w * 1_000_000))

    def write_uw(self, uw: int) -> None:
        v = msr.read(self.root, msr.PL3_CONTROL)
        if v is None:
            raise OSError("MSR 0x615 not readable")
        units = min(msr.PL3_LIMIT_MASK, max(0, int(round(uw / 1_000_000 / self.unit_w))))
        if not msr.write(self.root, msr.PL3_CONTROL, (v & ~msr.PL3_LIMIT_MASK) | units):
            raise OSError("MSR 0x615 not writable")

    @property
    def enabled(self) -> bool:
        v = msr.read(self.root, msr.PL3_CONTROL)
        return bool(v is not None and v & msr.PL3_ENABLE)


@dataclass(frozen=True)
class Result:
    ref: ChannelRef
    kind: str            # "written", "dry-run", "held", "log-only"
    value_mw: float      # engine value
    value_uw: int | None  # value after clamping, for targets
    written: bool


def find_zone(root: str, name: str) -> str | None:
    for d in sorted(glob.glob(os.path.join(root, "sys/class/powercap/intel-rapl:*"))):
        if ":" in os.path.basename(d)[len("intel-rapl:"):]:
            continue  # sub-zones such as intel-rapl:0:0
        if _read(os.path.join(d, "name")) == name:
            return d
    return None


def find_constraint(zone: str, name: str) -> str | None:
    for p in sorted(glob.glob(os.path.join(zone, "constraint_*_name"))):
        if _read(p) == name:
            return p[:-len("_name")] + "_power_limit_uw"
    return None


class OutputStage:
    def __init__(self, root: str, ranges: dict[ChannelRef, OutputChannel], dry_run: bool = False,
                 narrow: dict[str, float] | None = None, pl3: bool | None = None, pl4: bool | None = None,
                 mmio_zone: str | None = None):
        """pl3 / pl4: None = automatic, True = always where the target exists, False = never (device profile).
        mmio_zone: the `intel-rapl-mmio` zone that takes PL1, PL2 and the PL1 window (the mmio path, spec kernel-mmio-daemon 2);
        None: the MSR zone `intel-rapl:0` takes them (the msr path)."""
        self.root, self.ranges, self.dry_run = root, ranges, dry_run
        self.mmio_zone = mmio_zone
        self.notes: list[str] = []
        self.events: list[tuple[str, str]] = []      # (priority, text) for the log, drained by the daemon every cycle
        self._cut_logged: set[ChannelRef] = set()
        self.own_enable = False                      # the firmware's MMIO copy was off: this daemon writes enabled=1 (spec 4.2)
        self.enable_written = False                  # enabled is 1 through this daemon since the last restore (spec 10)
        self.enable_path = os.path.join(mmio_zone, "enabled") if mmio_zone else None
        self.targets: dict[ChannelRef, Target] = {}
        narrow = narrow or {}
        for name, value in narrow.items():
            if name not in HARD:
                raise ValueError(f"unknown target {name!r} in narrowing")
            if value > HARD[name][1]:
                raise ValueError(f"narrowing {name} to {value} W would widen the hard limit {HARD[name][1]} W")

        self.narrowed = {n for n, v in narrow.items() if v < HARD[n][1]}

        def bounds(name):
            lo, hi = HARD[name][0] * 1_000_000, HARD[name][1] * 1_000_000
            return lo, min(hi, int(narrow[name] * 1_000_000)) if name in narrow else hi

        for ch, (name, zone_name, cname) in CHANNELS.items():
            if name == "pl4" and pl4 is False:
                self.notes.append("pl4: off in the device profile, log-only")
                continue
            zone = mmio_zone if (mmio_zone and name in MMIO_NAMES) else find_zone(root, zone_name)
            path = find_constraint(zone, cname) if zone else None
            if not path:
                self.notes.append(f"{name}: no powercap target ({zone_name}/{cname}), log-only")
                continue
            if name == "pl4" and pl4 is None and not int(_read(path) or 0):
                # The SB3 reports 0 here; what a write does then is not verified (spec outputs 3) [D].
                self.notes.append("pl4: peak_power reads 0 on this system, log-only (a device profile can enable it)")
                continue
            self.targets[ChannelRef(LIMITS, ch)] = Target(name, path, *bounds(name))
        # PL1 time window (CF2F6076/61): written to the same powercap constraint as PL1, `long_term`.
        wzone = mmio_zone or find_zone(root, "package-0")
        wlimit = find_constraint(wzone, "long_term") if wzone else None
        wpath = wlimit[:-len("_power_limit_uw")] + "_time_window_us" if wlimit else None
        if wpath and os.path.exists(wpath):
            self.targets[ChannelRef(SOC, WINDOW_CHANNEL)] = WindowTarget("pl1_window", wpath, *WINDOW_HARD_US)
        else:
            self.notes.append("pl1 time window: no powercap target (package-0/long_term time window), log-only")
        # The mmio path: which targets are on the MMIO zone, the time unit for the window (spec 4.3), and the MSR copy's PL1, PL2 and
        # window, which stay at their snapshot values and are only watched (spec 3).
        self.mmio_refs = {r for r, t in self.targets.items() if mmio_zone and os.path.dirname(t.path) == mmio_zone}
        self.msr_watch: list[tuple[str, str, Target]] = []
        if mmio_zone:
            wref = ChannelRef(SOC, WINDOW_CHANNEL)
            if wref in self.targets:
                unit, src = rapl_copies.time_unit(root)
                if unit != 10:
                    del self.targets[wref]
                    self.mmio_refs.discard(wref)
                    what = f"time unit {unit} in {src}" if unit is not None else src
                    self.notes.append(f"pl1 time window: {what}, not 10 (1/1024 s) as the window steps assume, log-only")
            mzone = find_zone(root, "package-0")
            for label, cname in (("PL1", "long_term"), ("PL2", "short_term")):
                path = find_constraint(mzone, cname) if mzone else None
                if path:
                    self.msr_watch.append((label, path, Target(label, path, 0, 0)))
                    if cname == "long_term":
                        wp = path[:-len("_power_limit_uw")] + "_time_window_us"
                        if os.path.exists(wp):
                            self.msr_watch.append(("PL1 window", wp, WindowTarget("PL1 window", wp, 0, 0)))
        if pl3 is False:
            self.notes.append("pl3: off in the device profile, log-only")
        elif pl3 is not True:
            # Opt-in only: Windows' write of MSR 0x615 must be proven for the device (driver build or measurement).
            self.notes.append("pl3: not enabled in the device profile (MSR 0x615 not verified on this device), log-only")
        elif not msr.available(root):
            self.notes.append("pl3: MSR not available (no /dev/cpu/0/msr, no root, or kernel lockdown), log-only")
        else:
            unit = msr.power_unit_w(root)
            if unit is None:
                self.notes.append("pl3: MSR 0x606 not readable, log-only")
            else:
                self.targets[ChannelRef(LIMITS, PL3_CHANNEL)] = MsrTarget("pl3", "", *bounds("pl3"), root=root, unit_w=unit)

    def snapshot_keys(self) -> list[str]:
        """Every value this stage may change, for the firmware snapshot (files and MSRs)."""
        keys = []
        for t in self.targets.values():
            keys.append(t.snapshot_key)
            if not isinstance(t, (MsrTarget, WindowTarget)):
                keys.append(os.path.join(os.path.dirname(t.path), os.path.basename(t.path).split("_power")[0]
                                         + "_time_window_us"))
        if self.mmio_zone:
            keys += [path for _label, path, _t in self.msr_watch] + [self.enable_path]
        return list(dict.fromkeys(keys))

    # --- the mmio path (spec kernel-mmio-daemon 3, 4) ---

    def set_enable_owner(self, snapshot_values: dict[str, str]) -> None:
        """The daemon writes enabled=1 only where the firmware left the MMIO copy off (spec 4.2); Pro 8/9/10 have it on [M]."""
        if self.enable_path:
            v = snapshot_values.get(self.enable_path, _read(self.enable_path))
            self.own_enable = v == "0"

    def restore_msr_copy(self, snapshot_values: dict[str, str]) -> list[str]:
        """Spec 3: MSR PL1, PL2 and window back at their snapshot values where they differ beyond the tolerance. Returns what was
        set back ("PL1 13 W -> 30 W"); raises OSError when a write fails."""
        done = []
        for label, path, t in self.msr_watch:
            want, cur = snapshot_values.get(path), t.read_uw()
            if want is None or not want.isdigit() or cur is None:
                continue
            if abs(cur - int(want)) > t.tolerance(int(want)):
                _write(path, want)
                unit, div = ("s", 1e6) if isinstance(t, WindowTarget) else ("W", 1e6)
                done.append(f"{label} {cur / div:g} {unit} -> {int(want) / div:g} {unit}")
        return done

    def msr_copy_changes(self, snapshot_values: dict[str, str]) -> list["OutputStage.Foreign"]:
        """The MSR copy's PL1, PL2 and window against the snapshot (spec 10): a change lowers the limit in force."""
        found = []
        for label, path, t in self.msr_watch:
            want, cur = snapshot_values.get(path), t.read_uw()
            if want is None or not want.isdigit():
                continue
            if cur is None or abs(cur - int(want)) > t.tolerance(int(want)):
                found.append(self.Foreign(label, int(want), cur))
        return found

    def _enable(self) -> None:
        """Spec 4.1 step 3: where the copy is off and the daemon owns enable, write enabled=1 once and read it back."""
        cur = _read(self.enable_path)
        if cur is None:
            raise PathFailure(f"{self.enable_path} not readable (MMIO zone gone)", marker=False)
        if cur != "0":
            self.enable_written = True
            return
        try:
            _write(self.enable_path, "1")
        except OSError as e:
            raise PathFailure(f"enabled=1 failed ({e.strerror or e})") from e
        if _read(self.enable_path) != "1":
            raise PathFailure("enabled=1 written but enabled reads 0 (PL1 enable did not stick)")
        self.enable_written = True
        self.events.append(("notice", "MMIO copy: enabled=1 written (sets PL1/PL2 enable and clamp)"))
        self._check_bits()

    def _check_bits(self) -> None:
        """Spec 4.2: `enabled=1` proves PL1 enable only; where the register is readable, check all four bits."""
        unit = msr.power_unit_w(self.root)
        copy, _why = rapl_copies.read_mmio(self.root, unit) if unit else (None, None)
        if copy is None:
            return
        bits = {"PL1 enable": copy.pl1[1], "PL1 clamp": copy.pl1[2], "PL2 enable": copy.pl2[1], "PL2 clamp": copy.pl2[2]}
        missing = [n for n, v in bits.items() if not v]
        if missing:
            self.events.append(("warning", f"MMIO copy after enabled=1: {', '.join(missing)} not set ({copy.describe()})"))

    def check_zone(self) -> None:
        """Spec 2.6: the MMIO zone is gone (module unloaded): the path cannot go on; a restart chooses again (no marker)."""
        if self.mmio_zone and self.mmio_refs and not os.path.isdir(self.mmio_zone):
            raise PathFailure(f"{self.mmio_zone} is gone (module unloaded?)", marker=False)

    def drain_events(self) -> list[tuple[str, str]]:
        out, self.events = self.events, []
        return out

    def kind(self, ref: ChannelRef) -> str:
        if ref in self.targets:
            return "dry-run" if self.dry_run else "written"
        return "log-only"

    def clamp_uw(self, ref: ChannelRef, value_mw: float) -> int:
        t = self.targets[ref]
        v = value_mw
        rng = self.ranges.get(ref)
        if rng is not None:
            v = min(max(v, rng.min), rng.max)
        uw = int(round(v * 1000))
        bounded = min(max(uw, t.hard_min_uw), t.hard_max_uw)
        if bounded != uw and ref in self.mmio_refs and ref not in self._cut_logged:
            # Spec kernel-mmio-daemon 6: the RAPL core wraps values from 4096 W silently, the module has no upper bound.
            self._cut_logged.add(ref)
            if uw > t.hard_max_uw and t.name in self.narrowed:
                # The profile's narrowing (outputs.narrow), not the hard limit: intended, so a notice (seen on the SB3, B12 8.5).
                self.events.append(("notice", f"{t.name.upper()}: computed {uw / 1e6:g} W, written {bounded / 1e6:g} W "
                                              f"(narrowed by the device profile to {t.hard_max_uw / 1e6:g} W)"))
            else:
                self.events.append(("warning", f"{t.name.upper()}: computed {uw / 1e6:g} W is outside the hard limit "
                                               f"{t.hard_min_uw / 1e6:g}-{t.hard_max_uw / 1e6:g} W, written {bounded / 1e6:g} W"))
        return t.shape(bounded)

    @staticmethod
    def _order(ref: ChannelRef) -> int:
        """Window, then PL1, then PL2, then the rest (spec kernel-mmio-daemon 4.1; supervisor 3: never PL1 with a foreign window)."""
        if ref == ChannelRef(SOC, WINDOW_CHANNEL):
            return 0
        return {51: 1, 52: 2}.get(ref.channel, 3) if ref.guid == LIMITS else 3

    def apply(self, values: dict[ChannelRef, float], hold: frozenset = frozenset()) -> dict[ChannelRef, Result]:
        """Clamp, convert and write the values. Channels in `hold` keep their current value (e.g. PL4 while its EC
        inputs are invalid, spec outputs 3). Returns one result per channel."""
        out = {}
        if not self.dry_run:
            self.check_zone()
        for ref in sorted(values, key=self._order):
            mw = values[ref]
            t = self.targets.get(ref)
            if t is None:
                out[ref] = Result(ref, "log-only", mw, None, False)
                continue
            uw = self.clamp_uw(ref, mw)
            if ref in hold:
                out[ref] = Result(ref, "held", mw, uw, False)
                continue
            if self.dry_run:
                out[ref] = Result(ref, "dry-run", mw, uw, False)
                continue
            written = False
            current = t.read_uw()
            tol = t.tolerance(uw)
            if t.changed(uw) or current is None or abs(current - uw) > tol:
                try:
                    t.write_uw(uw)
                except OSError as e:
                    if ref in self.mmio_refs:
                        gone = not os.path.isdir(self.mmio_zone)
                        raise PathFailure(f"{t.name}: write to {t.path} failed ({e.strerror or e})", marker=not gone) from e
                    self.notes.append(f"{t.name}: write failed ({e}), log-only from now on")
                    del self.targets[ref]
                    out[ref] = Result(ref, "log-only", mw, None, False)
                    continue
                written = True
                back = t.read_uw()
                t.last_uw = back if back is not None else uw
                t.wrote_uw = uw
            out[ref] = Result(ref, "written", mw, uw, written)
        if self.own_enable and not self.dry_run and any(r in out and out[r].kind == "written" for r in self.mmio_refs):
            self._enable()
        return out

    @dataclass(frozen=True)
    class Foreign:
        ref: ChannelRef | str
        expected_uw: int
        found_uw: int | None

    def foreign_changes(self) -> list["OutputStage.Foreign"]:
        """Targets whose current value differs from what we last wrote (spec supervisor 5)."""
        found = []
        for ref, t in self.targets.items():
            if t.last_uw is None:
                continue
            cur_uw = t.read_uw()
            if cur_uw is not None and t.wrote_uw is not None and abs(cur_uw - t.wrote_uw) <= t.tolerance(t.wrote_uw):
                # The register now holds what we wrote: no other writer, even if the read-back right after the write still
                # showed the old value (Pro 10, MSR 0x615: read 0 at once, 130 W a cycle later [M, #2383]).
                t.last_uw = cur_uw
                continue
            if cur_uw is None or abs(cur_uw - t.last_uw) > t.tolerance(t.last_uw):
                found.append(self.Foreign(ref, t.last_uw, cur_uw))
        if self.own_enable and self.enable_written and _read(self.enable_path) == "0":
            # Spec kernel-mmio-daemon 10: thermald's RAPL cooling device or its workaround writes enabled=0 [C]. The next apply sets
            # it again (overwritten like a limit); enabled of a copy the firmware had on is never watched (the daemon never set it).
            found.append(self.Foreign("enabled", 1, 0))
        return found

    def forget(self) -> None:
        """After a restore to firmware values: we no longer own the current values."""
        for t in self.targets.values():
            t.last_uw = t.wrote_uw = None
        self.enable_written = False
