"""Where PL1, PL2 and the PL1 time window go: the MMIO copy or the MSR copy (spec kernel-mmio-daemon.md 2).

Chosen once at start, before the snapshot, and not changed while the daemon runs. Windows writes the MMIO copy (MCHBAR + 0x59a0,
channels 51/52/61) [C, research/15 1]; Linux reaches it only through an `intel-rapl-mmio` zone: the one of the module
`surface_rapl_mmio` (written only with its parameter allow_write=Y) or the one of the processor thermal driver (PCI 00:04.0), which
counts as writable (maintainer's decision 24.11.2026, spec 13.1). PL4 and psys stay on their MSR zones on either path (spec 2.3).
"""
import glob
import os
from dataclasses import dataclass

MODULE = "sys/module/surface_rapl_mmio"
ALLOW_WRITE = MODULE + "/parameters/allow_write"
PROC_THERMAL = "sys/bus/pci/devices/0000:00:04.0"
MARKER = "mmio-unusable"          # in the run directory: the MMIO path failed earlier in this boot (spec 4.1)


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


@dataclass(frozen=True)
class PlPath:
    kind: str                 # "mmio" or "msr"
    reason: str
    zone: str | None = None   # the MMIO zone directory on the mmio path
    source: str | None = None  # "module" or "kernel" on the mmio path

    def describe(self) -> str:
        return f"PL path {self.kind}: {self.reason}"


def find_mmio_zone(root: str) -> str | None:
    """The top-level `intel-rapl-mmio` zone named package-0 (sub-zones such as `intel-rapl-mmio:0:0` are DRAM)."""
    for d in sorted(glob.glob(os.path.join(root, "sys/class/powercap/intel-rapl-mmio:*"))):
        if ":" in os.path.basename(d)[len("intel-rapl-mmio:"):]:
            continue
        if _read(os.path.join(d, "name")) == "package-0":
            return d
    return None


def _constraints(zone: str) -> set[str]:
    return {_read(p) for p in glob.glob(os.path.join(zone, "constraint_*_name"))}


def writable_zone(root: str) -> tuple[str | None, str | None, str]:
    """(zone, source, reason): a writable MMIO zone with long_term and short_term, or (None, None, why not)."""
    zone = find_mmio_zone(root)
    if zone is None:
        return None, None, "no MMIO zone"
    name = os.path.basename(zone)
    if not {"long_term", "short_term"} <= _constraints(zone):
        return None, None, f"{name} has no long_term/short_term constraint"
    if os.path.isdir(os.path.join(root, MODULE)):
        allow = _read(os.path.join(root, ALLOW_WRITE))
        if allow not in ("Y", "1"):
            return None, None, f"{name} of surface_rapl_mmio is read-only (allow_write={allow or '?'})"
        return zone, "module", f"{name} of surface_rapl_mmio, allow_write=Y"
    if os.path.isdir(os.path.join(root, PROC_THERMAL)):
        return zone, "kernel", f"{name} of the processor thermal device 00:04.0 (the kernel's zone)"
    return None, None, f"{name} of unknown origin (neither surface_rapl_mmio nor 00:04.0)"


def choose(root: str, run_dir: str, profile_mmio: bool | None) -> PlPath:
    """Spec 2.1: mmio only with a writable zone, no failure marker and no profile switch-off; else msr."""
    zone, source, why = writable_zone(root)
    if zone is None:
        return PlPath("msr", why)
    if profile_mmio is False:
        return PlPath("msr", f"{why}, but off in the device profile (outputs.mmio = false)")
    marker = os.path.join(run_dir, MARKER)
    if os.path.exists(marker):
        return PlPath("msr", f"{why}, but {marker} exists: the MMIO path failed earlier in this boot "
                             f"({_read(marker) or 'no reason recorded'})")
    return PlPath("mmio", why, zone, source)
