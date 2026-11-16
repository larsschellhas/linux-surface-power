"""Throttle requests `9BBB8910` (spec outputs 8): passive-cooling requests in percent, written as frequency caps.

On Windows each request goes to one device that the ACPI method `OPTS` of the thermal zone `TPOL` lists
(`PoSetThermalPassiveCooling`; 100 = no throttling). Linux has no such call. The request is translated into a cap on the
maximum frequency of the matching device: a cpufreq policy (`scaling_max_freq`) or a GPU (i915, xe or devfreq).
Several requests on one target combine by the minimum. Nothing is written without a matching target on this machine.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from ..loader.model import ChannelRef

THROTTLE = "9BBB8910-0CC7-4C01-9FEB-DBFFF6574EFC"
PARK = ChannelRef(THROTTLE, 62)

# What `OPTS` returns, from the public ACPI dumps (linux-surface/acpidumps), per group of products [M]. The order is the order
# of the list. The x86 products return only the Processor Aggregator Device (ACPI000C); there the blob has only `/62`.
# The Laptop 3 AMD dump has no `OPTS` at all, and its blob has no `9BBB8910` output.
OPTS = {
    "x86 (Intel and AMD): Book 3, Go 3, Go 4, Laptop 4, 5, 6, Laptop Go 1-3, Laptop Studio 1, 2, Laptop SE, Pro 7+, 8, 9, "
    "10 for Business": ("\\_SB.PAGD",),
    "Pro X (SQ1, SQ2)": ("\\_SB.SYSM.CLUS.CPU4", "\\_SB.SYSM.CLUS.CPU5", "\\_SB.SYSM.CLUS.CPU6", "\\_SB.SYSM.CLUS.CPU7",
                         "\\_SB.GPU0"),
    "Pro 9 5G, Windows Dev Kit 2023 (Snapdragon 8cx Gen 3)": (
        "\\_SB.SYSM.CLUS.CPU0", "\\_SB.SYSM.CLUS.CPU1", "\\_SB.SYSM.CLUS.CPU2", "\\_SB.SYSM.CLUS.CPU3",
        "\\_SB.SYSM.CLUS.CPU4", "\\_SB.SYSM.CLUS.CPU5", "\\_SB.SYSM.CLUS.CPU6", "\\_SB.SYSM.CLUS.CPU7",
        "\\_SB.GPU0", "\\_SB.PAGD", "\\_SB.MSKN"),
    "Pro 11 (Snapdragon), Laptop 7 (Snapdragon)": (
        "\\_SB.SYSM.CLS0.CPU0", "\\_SB.SYSM.CLS1.CPU0", "\\_SB.SYSM.CLS2.CPU0", "\\_SB.GPU0", "\\_SB.AGR0", "\\_SB.MSKN",
        "\\_SB.CSW0", "\\_SB.MJCT", "\\_SB.MBJ0"),
}

# Blob channel -> device role. The channel numbers are not OPTS indices (an Intel blob has `/62` for a one-entry list). The driver
# holds a fixed table "OPTS entry name -> channel" and compares every entry of the list with it [C, thermal-policy driver of the
# Pro 10 and Pro 9 packages]: CLUS.CPU4-7 -> 1-4, CLUS.CPU0-3 -> 6-9, CLS0-2.CPU0 -> 20-22, GPU0 -> 5, PAGD and AGR0 -> 62, MSKN,
# MJCT, MBJ0 -> 103-105, CSW0 -> 200. (The blob names `O_WTMF_CPU0-3` for channels 1-4 are labels only: those channels reach CPU4-7.)
# That Windows CPU n is Linux CPU n, and that the clusters follow the numeric order of the policies, is [H].
#   ("cpu", n)      CPU n of the Windows list (`CLUS.CPUn`)               -> the cpufreq policy that contains Linux CPU n
#   ("cluster", k)  cluster k (`CLSk.CPU0`)                               -> the k-th cpufreq policy, in numeric order
#   ("gpu",)        `GPU0`                                                -> Intel i915/xe or a devfreq GPU
#   ("park",)       the Processor Aggregator Device (`PAGD`, `AGR0`)      -> all cpufreq policies (4)
#   None            modem sensors, NPU: no Linux frequency interface known -> log-only
ROLES = {1: ("cpu", 4), 2: ("cpu", 5), 3: ("cpu", 6), 4: ("cpu", 7), 6: ("cpu", 0), 7: ("cpu", 1), 8: ("cpu", 2), 9: ("cpu", 3),
         5: ("gpu",),
         20: ("cluster", 0), 21: ("cluster", 1), 22: ("cluster", 2), 62: ("park",),
         103: None, 104: None, 105: None, 200: None}

FLOOR_PCT = 20           # hard lower bound [D]: no request caps a device below 20 % of its maximum frequency
STEP_PCT = 2             # a new cap that differs by less than 2 % of the maximum is not written [D]


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _int(path: str) -> int | None:
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


@dataclass
class FreqTarget:
    """One file that caps a frequency, with its limits. Units are those of the file (kHz, MHz or Hz)."""
    name: str                 # "cpufreq policy0", "GPU i915 card0", ...
    kind: str                 # "cpufreq" or "gpu"
    path: str                 # the file that is written
    lo_path: str | None       # the matching minimum-frequency file, never capped below it
    hw_lo: int
    hw_hi: int
    cpus: frozenset[int] = frozenset()
    baseline: int | None = None   # the value before the first write (firmware or user): "100 %" means this
    last: int | None = None       # last value we wrote or accepted, as read back
    would: int | None = None      # dry-run: what would be written
    failed: bool = False

    @property
    def snapshot_key(self) -> str:
        return self.path

    def read(self) -> int | None:
        return _int(self.path)

    def floor(self) -> int:
        lo = self.hw_lo
        if self.lo_path:
            lo = max(lo, _int(self.lo_path) or 0)
        return min(max(lo, int(self.hw_hi * FLOOR_PCT / 100)), self.hw_hi)

    def desired(self, pct: float) -> int:
        """The cap for a request: pct of the maximum frequency, within [floor, baseline] (spec outputs 8)."""
        top = min(self.baseline or self.hw_hi, self.hw_hi)
        if pct >= 100:
            return top
        v = int(round(self.hw_hi * max(0.0, pct) / 100))
        return min(max(v, self.floor()), top)

    @property
    def step(self) -> int:
        return max(1, int(self.hw_hi * STEP_PCT / 100))


@dataclass(frozen=True)
class ThrottleResult:
    ref: ChannelRef
    kind: str            # "written", "dry-run", "log-only"
    pct: float
    targets: tuple[str, ...]
    written: bool


@dataclass(frozen=True)
class Foreign:
    target: str
    expected: int
    found: int | None


def find_cpu_policies(root: str) -> list[FreqTarget]:
    out = []
    dirs = glob.glob(os.path.join(root, "sys/devices/system/cpu/cpufreq/policy[0-9]*"))
    for d in sorted(dirs, key=lambda p: int(os.path.basename(p)[len("policy"):])):
        lo, hi = _int(os.path.join(d, "cpuinfo_min_freq")), _int(os.path.join(d, "cpuinfo_max_freq"))
        path = os.path.join(d, "scaling_max_freq")
        if lo is None or hi is None or hi <= 0 or _int(path) is None:
            continue
        cpus = set()
        for part in (_read(os.path.join(d, "related_cpus")) or os.path.basename(d)[len("policy"):]).split():
            cpus.add(int(part)) if part.isdigit() else None
        out.append(FreqTarget(f"cpufreq {os.path.basename(d)}", "cpufreq", path, os.path.join(d, "scaling_min_freq"),
                              lo, hi, frozenset(cpus), baseline=_int(path)))
    return out


def _card_dirs(root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(root, "sys/class/drm/card[0-9]*")))


def find_gpu(root: str) -> FreqTarget | None:
    """Intel i915 or xe first; else a devfreq device with `gpu` in its name (Adreno). NVIDIA and AMD have no entry here."""
    for card in _card_dirs(root):
        name = os.path.basename(card)
        # i915, older layout and per-GT layout
        for mx, rp0, rpn, mn in (("gt_max_freq_mhz", "gt_RP0_freq_mhz", "gt_RPn_freq_mhz", "gt_min_freq_mhz"),
                                 ("gt/gt0/rps_max_freq_mhz", "gt/gt0/rps_RP0_freq_mhz", "gt/gt0/rps_RPn_freq_mhz",
                                  "gt/gt0/rps_min_freq_mhz")):
            hi, lo, cur = _int(os.path.join(card, rp0)), _int(os.path.join(card, rpn)), _int(os.path.join(card, mx))
            if hi and lo is not None and cur is not None:
                return FreqTarget(f"GPU i915 {name}", "gpu", os.path.join(card, mx), os.path.join(card, mn), lo, hi,
                                  baseline=cur)
        # xe
        x = os.path.join(card, "device/tile0/gt0/freq0")
        hi, lo, cur = _int(os.path.join(x, "rp0_freq")), _int(os.path.join(x, "rpn_freq")), _int(os.path.join(x, "max_freq"))
        if hi and lo is not None and cur is not None:
            return FreqTarget(f"GPU xe {name}", "gpu", os.path.join(x, "max_freq"), os.path.join(x, "min_freq"), lo, hi,
                              baseline=cur)
    for d in sorted(glob.glob(os.path.join(root, "sys/class/devfreq/*"))):
        if "gpu" not in os.path.basename(d).lower():
            continue
        freqs = [int(x) for x in (_read(os.path.join(d, "available_frequencies")) or "").split() if x.isdigit()]
        cur = _int(os.path.join(d, "max_freq"))
        if freqs and cur is not None:
            return FreqTarget(f"GPU devfreq {os.path.basename(d)}", "gpu", os.path.join(d, "max_freq"),
                              os.path.join(d, "min_freq"), min(freqs), max(freqs), baseline=cur)
    return None


class ThrottleStage:
    """The write path of the throttle requests. Built for the channels the blob has; only those get targets."""

    def __init__(self, root: str, channels, dry_run: bool = False):
        self.root, self.dry_run = root, dry_run
        self.notes: list[str] = []
        self.targets: list[FreqTarget] = []
        self.map: dict[ChannelRef, list[FreqTarget]] = {}
        self.channels = frozenset(r for r in channels if r.guid == THROTTLE)
        if not self.channels:
            return
        policies = find_cpu_policies(root)
        gpu = None
        if any((ROLES.get(r.channel) or ("none",))[0] == "gpu" for r in self.channels):
            gpu = find_gpu(root)
        for r in sorted(self.channels):
            role = ROLES.get(r.channel)
            found: list[FreqTarget] = []
            if role is None:
                self.notes.append(f"{r}: no Linux frequency interface for this device, log-only")
            elif role[0] == "park":
                found = list(policies)
            elif role[0] == "cpu":
                found = [p for p in policies if role[1] in p.cpus]
            elif role[0] == "cluster":
                found = policies[role[1]:role[1] + 1]
            elif role[0] == "gpu":
                found = [gpu] if gpu else []
            if role is not None and not found:
                self.notes.append(f"{r}: no matching {'GPU frequency interface' if role[0] == 'gpu' else 'cpufreq policy'}, log-only")
            if found:
                self.map[r] = found
                for t in found:
                    if t not in self.targets:
                        self.targets.append(t)
        if self.targets:
            self.notes.append("throttle requests write scaling_max_freq / GPU maximum frequency; thermald, power-profiles-daemon "
                              "or the user may write the same files: a change by another process is reported")

    # --- identity ---
    def has_target(self, ref: ChannelRef) -> bool:
        return any(not t.failed for t in self.map.get(ref, ()))

    def kind(self, ref: ChannelRef) -> str:
        if not self.has_target(ref):
            return "log-only"
        return "dry-run" if self.dry_run else "written"

    def describe(self, ref: ChannelRef) -> str:
        """Short text for the status file and the report."""
        ts = [t for t in self.map.get(ref, ()) if not t.failed]
        if not ts:
            return "no Linux path"
        return "GPU freq cap" if ts[0].kind == "gpu" else "cpufreq cap"

    def snapshot_keys(self) -> list[str]:
        return [t.snapshot_key for t in self.targets]

    def set_baseline(self, values: dict[str, str]) -> None:
        """Use the values of the firmware snapshot (a predecessor may have left a cap in the files)."""
        for t in self.targets:
            v = values.get(t.snapshot_key)
            if v is not None and v.isdigit():
                t.baseline = int(v)

    # --- apply ---
    def apply(self, values: dict[ChannelRef, float], hold: frozenset = frozenset()) -> dict[ChannelRef, ThrottleResult]:
        requests: dict[int, float] = {}                 # id(target) -> minimum request in percent
        by_id = {id(t): t for t in self.targets}
        out: dict[ChannelRef, ThrottleResult] = {}
        for ref, pct in values.items():
            if ref.guid != THROTTLE:
                continue
            ts = [t for t in self.map.get(ref, ()) if not t.failed]
            if not ts:
                out[ref] = ThrottleResult(ref, "log-only", pct, (), False)
                continue
            for t in ts:
                requests[id(t)] = min(requests.get(id(t), 100.0), min(max(pct, 0.0), 100.0))
        wrote: set[int] = set()
        for tid, pct in requests.items():
            t = by_id[tid]
            want = t.desired(pct)
            if self.dry_run:
                t.would = want
                continue
            current = t.read()
            if current is None:
                continue                                 # the policy is gone (CPU offline): try again next cycle
            if want != current and (abs(want - current) >= t.step or want == t.desired(100)):
                try:
                    with open(t.path, "w") as f:
                        f.write(str(want))
                except FileNotFoundError:
                    continue
                except OSError as e:
                    t.failed = True
                    self.notes.append(f"{t.name}: write failed ({e}), log-only from now on")
                    continue
                wrote.add(tid)
                back = t.read()
                t.last = back if back is not None else want
            else:
                t.last = current
        for ref, pct in values.items():
            if ref.guid != THROTTLE or ref in out:
                continue
            ts = [t for t in self.map.get(ref, ()) if not t.failed]
            if not ts:
                out[ref] = ThrottleResult(ref, "log-only", pct, (), False)
                continue
            out[ref] = ThrottleResult(ref, "dry-run" if self.dry_run else "written", pct,
                                      tuple(t.name for t in ts), any(id(t) in wrote for t in ts))
        return out

    def foreign_changes(self) -> list[Foreign]:
        """Targets whose current value differs from what we last wrote (more than one step: the hardware rounds)."""
        found = []
        for t in self.targets:
            if t.last is None or t.failed:
                continue
            cur = t.read()
            if cur is None:
                continue
            if abs(cur - t.last) >= t.step:
                found.append(Foreign(t.name, t.last, cur))
        return found

    def forget(self) -> None:
        """After a restore to firmware values: we no longer own the current values."""
        for t in self.targets:
            t.last = None
            t.would = None

    def status(self) -> list[dict]:
        out = []
        for t in self.targets:
            out.append({"target": t.name, "path": t.path, "baseline": t.baseline, "hw": t.read(), "would": t.would,
                        "failed": t.failed})
        return out
