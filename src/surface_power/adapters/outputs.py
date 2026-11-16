"""Output stage (spec outputs.md): the single write path for limits (NFR-2).

Order for every value: clamp to the blob's output range, to the hard limits and to the user's narrowing; convert mW -> µW;
write only on a change; read back. In dry-run nothing is written.
The powercap `max_power_uw` is not used as a bound: on the SB3 it reports the 15 W TDP while the firmware PL1 is 30 W [M].
"""
import glob
import os
from dataclasses import dataclass

from ..loader.model import ChannelRef, OutputChannel
from . import msr

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
# Hardware rounding tolerance for foreign-change detection (spec supervisor 5).
TOLERANCE_UW = 200_000


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

    @property
    def snapshot_key(self) -> str:
        return self.path

    def read_uw(self) -> int | None:
        v = _read(self.path)
        return int(v) if v and v.isdigit() else None

    def write_uw(self, uw: int) -> None:
        with open(self.path, "w") as f:
            f.write(str(uw))


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
                 narrow: dict[str, float] | None = None, pl3: bool | None = None, pl4: bool | None = None):
        """pl3 / pl4: None = automatic, True = always where the target exists, False = never (device profile)."""
        self.root, self.ranges, self.dry_run = root, ranges, dry_run
        self.notes: list[str] = []
        self.targets: dict[ChannelRef, Target] = {}
        narrow = narrow or {}
        for name, value in narrow.items():
            if name not in HARD:
                raise ValueError(f"unknown target {name!r} in narrowing")
            if value > HARD[name][1]:
                raise ValueError(f"narrowing {name} to {value} W would widen the hard limit {HARD[name][1]} W")

        def bounds(name):
            lo, hi = HARD[name][0] * 1_000_000, HARD[name][1] * 1_000_000
            return lo, min(hi, int(narrow[name] * 1_000_000)) if name in narrow else hi

        for ch, (name, zone_name, cname) in CHANNELS.items():
            if name == "pl4" and pl4 is False:
                self.notes.append("pl4: off in the device profile, log-only")
                continue
            zone = find_zone(root, zone_name)
            path = find_constraint(zone, cname) if zone else None
            if not path:
                self.notes.append(f"{name}: no powercap target ({zone_name}/{cname}), log-only")
                continue
            if name == "pl4" and pl4 is None and not int(_read(path) or 0):
                # The SB3 reports 0 here; what a write does then is not verified (spec outputs 3) [D].
                self.notes.append("pl4: peak_power reads 0 on this system, log-only (a device profile can enable it)")
                continue
            self.targets[ChannelRef(LIMITS, ch)] = Target(name, path, *bounds(name))
        if pl3 is False:
            self.notes.append("pl3: off in the device profile, log-only")
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
            if not isinstance(t, MsrTarget):
                keys.append(os.path.join(os.path.dirname(t.path), os.path.basename(t.path).split("_power")[0]
                                         + "_time_window_us"))
        return keys

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
        return min(max(uw, t.hard_min_uw), t.hard_max_uw)

    def apply(self, values: dict[ChannelRef, float], hold: frozenset = frozenset()) -> dict[ChannelRef, Result]:
        """Clamp, convert and write the values. Channels in `hold` keep their current value (e.g. PL4 while its EC
        inputs are invalid, spec outputs 3). Returns one result per channel."""
        out = {}
        for ref, mw in values.items():
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
            if t.last_uw is None or abs(t.last_uw - uw) > TOLERANCE_UW or current is None or abs(current - uw) > TOLERANCE_UW:
                try:
                    t.write_uw(uw)
                except OSError as e:
                    self.notes.append(f"{t.name}: write failed ({e}), log-only from now on")
                    del self.targets[ref]
                    out[ref] = Result(ref, "log-only", mw, None, False)
                    continue
                written = True
                back = t.read_uw()
                t.last_uw = back if back is not None else uw
            out[ref] = Result(ref, "written", mw, uw, written)
        return out

    @dataclass(frozen=True)
    class Foreign:
        ref: ChannelRef
        expected_uw: int
        found_uw: int | None

    def foreign_changes(self) -> list["OutputStage.Foreign"]:
        """Targets whose current value differs from what we last wrote (spec supervisor 5)."""
        found = []
        for ref, t in self.targets.items():
            if t.last_uw is None:
                continue
            cur_uw = t.read_uw()
            if cur_uw is None or abs(cur_uw - t.last_uw) > TOLERANCE_UW:
                found.append(self.Foreign(ref, t.last_uw, cur_uw))
        return found

    def forget(self) -> None:
        """After a restore to firmware values: we no longer own the current values."""
        for t in self.targets.values():
            t.last_uw = None
