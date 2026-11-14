"""Output stage (spec outputs.md): the single write path for limits (NFR-2).

Order for every value: clamp to the blob's output range, to the hard limits and to the user's narrowing; convert mW -> µW;
write only on a change; read back. In dry-run nothing is written.
The powercap `max_power_uw` is not used as a bound: on the SB3 it reports the 15 W TDP while the firmware PL1 is 30 W [M].
"""
import glob
import os
from dataclasses import dataclass

from ..loader.model import ChannelRef, OutputChannel

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
    name: str
    path: str
    hard_min_uw: int
    hard_max_uw: int
    last_uw: int | None = None     # last value we wrote, as read back


@dataclass(frozen=True)
class Result:
    ref: ChannelRef
    kind: str            # "written", "dry-run", "log-only"
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
                 narrow: dict[str, float] | None = None, pl3: bool = False, pl4: bool = False):
        self.root, self.ranges, self.dry_run = root, ranges, dry_run
        self.notes: list[str] = []
        self.targets: dict[ChannelRef, Target] = {}
        narrow = narrow or {}
        for name, value in narrow.items():
            if name not in HARD:
                raise ValueError(f"unknown target {name!r} in narrowing")
            if value > HARD[name][1]:
                raise ValueError(f"narrowing {name} to {value} W would widen the hard limit {HARD[name][1]} W")
        for ch, (name, zone_name, cname) in CHANNELS.items():
            if name == "pl4" and not pl4:
                continue  # off by default: the SB3 reports 0 for peak_power, its meaning is not verified [D]
            zone = find_zone(root, zone_name)
            path = find_constraint(zone, cname) if zone else None
            if not path:
                self.notes.append(f"{name}: no powercap target ({zone_name}/{cname}), log-only")
                continue
            lo, hi = HARD[name][0] * 1_000_000, HARD[name][1] * 1_000_000
            if name in narrow:
                hi = min(hi, int(narrow[name] * 1_000_000))
            self.targets[ChannelRef(LIMITS, ch)] = Target(name, path, lo, hi)
        if pl3:
            self.notes.append("PL3: MSR writes are not implemented yet, log-only" if self._msr_available()
                              else "PL3: MSR not available (no /dev/cpu/0/msr or kernel lockdown), log-only")

    def _msr_available(self) -> bool:
        lockdown = _read(os.path.join(self.root, "sys/kernel/security/lockdown")) or ""
        return os.path.exists(os.path.join(self.root, "dev/cpu/0/msr")) and "[none]" in lockdown

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

    def apply(self, values: dict[ChannelRef, float]) -> dict[ChannelRef, Result]:
        """Clamp, convert and write the values. Returns one result per channel."""
        out = {}
        for ref, mw in values.items():
            t = self.targets.get(ref)
            if t is None:
                out[ref] = Result(ref, "log-only", mw, None, False)
                continue
            uw = self.clamp_uw(ref, mw)
            if self.dry_run:
                out[ref] = Result(ref, "dry-run", mw, uw, False)
                continue
            written = False
            current = _read(t.path)
            if t.last_uw is None or abs(t.last_uw - uw) > TOLERANCE_UW or current is None or abs(int(current) - uw) > TOLERANCE_UW:
                with open(t.path, "w") as f:
                    f.write(str(uw))
                written = True
                back = _read(t.path)
                t.last_uw = int(back) if back and back.isdigit() else uw
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
            cur = _read(t.path)
            cur_uw = int(cur) if cur and cur.isdigit() else None
            if cur_uw is None or abs(cur_uw - t.last_uw) > TOLERANCE_UW:
                found.append(self.Foreign(ref, t.last_uw, cur_uw))
        return found

    def forget(self) -> None:
        """After a restore to firmware values: we no longer own the current values."""
        for t in self.targets.values():
            t.last_uw = None
