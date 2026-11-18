"""The copies of the PL1/PL2 limits, read only (spec outputs 3.3, deviation DV-8).

Windows writes PL1/PL2 to the MMIO copy (MCHBAR + 0x59A0); this daemon writes the MSR copy (0x610) through powercap. The lower
enabled copy is in force (measured on a Pro 10, linux-surface#2383; as a general rule [H]). This module only reads: it writes nothing.

MCHBAR is the 64-bit register at config offset 0x48 of the host bridge 00:00.0, bit 0 = enabled. The MMIO copy has the layout of
MSR 0x610: PL1 bits 14:0, enable bit 15, clamp bit 16; PL2 the same from bit 32; lock bit 63; the unit is MSR 0x606 bits 3:0.
Reading /dev/mem and the MSR needs root; both can fail (kernel lockdown, STRICT_DEVMEM), then the value is None.
"""
import glob
import mmap
import os
import struct
from dataclasses import dataclass

from . import msr

MCHBAR_CONFIG = "sys/bus/pci/devices/0000:00:00.0/config"
MCHBAR_OFFSET = 0x48
MMIO_PL_OFFSET = 0x59A0
MSR_PL = 0x610
PROC_THERMAL = "sys/bus/pci/devices/0000:00:04.0"


@dataclass(frozen=True)
class Copy:
    """One copy of PL1/PL2: raw register value and its decoding."""
    raw: int
    unit_w: float

    def _half(self, x: int) -> tuple[float, bool, bool]:
        return (x & 0x7FFF) * self.unit_w, bool(x >> 15 & 1), bool(x >> 16 & 1)

    @property
    def pl1(self) -> tuple[float, bool, bool]:
        """(limit in W, enabled, clamp)"""
        return self._half(self.raw)

    @property
    def pl2(self) -> tuple[float, bool, bool]:
        return self._half(self.raw >> 32)

    @property
    def lock(self) -> bool:
        return bool(self.raw >> 63)

    def describe(self) -> str:
        def one(name, h):
            return f"{name} {h[0]:g} W ({'on' if h[1] else 'off'}{', clamp' if h[2] else ''})"
        return f"{one('PL1', self.pl1)}, {one('PL2', self.pl2)}" + (", locked" if self.lock else "")


def _mmio64(phys: int, root: str) -> int | None:
    page = phys & ~(mmap.PAGESIZE - 1)
    try:
        fd = os.open(os.path.join(root, "dev/mem"), os.O_RDONLY | os.O_SYNC)
    except OSError:
        return None
    try:
        m = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED, mmap.PROT_READ, offset=page)
        try:
            return struct.unpack("<Q", m[phys - page:phys - page + 8])[0]
        finally:
            m.close()
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def mchbar(root: str) -> int | None:
    try:
        with open(os.path.join(root, MCHBAR_CONFIG), "rb") as f:
            f.seek(MCHBAR_OFFSET)
            raw = f.read(8)
    except OSError:
        return None
    if len(raw) != 8:
        return None
    v = struct.unpack("<Q", raw)[0]
    return (v & ~0x7FFF & ((1 << 39) - 1)) if v & 1 else None


def has_proc_thermal(root: str) -> bool:
    """The Processor Thermal PCI device 00:04.0 (driver processor_thermal_device_pci, which provides `intel-rapl-mmio`)."""
    return os.path.isdir(os.path.join(root, PROC_THERMAL))


def has_rapl_mmio_zone(root: str) -> bool:
    return bool(glob.glob(os.path.join(root, "sys/class/powercap/intel-rapl-mmio:*")))


def read_mmio(root: str, unit_w: float) -> Copy | None:
    base = mchbar(root)
    if base is None:
        return None
    v = _mmio64(base + MMIO_PL_OFFSET, root)
    return None if v is None else Copy(v, unit_w)


def read_msr(root: str, unit_w: float) -> Copy | None:
    v = msr.read(root, MSR_PL)
    return None if v is None else Copy(v, unit_w)


def read_all(root: str) -> dict | None:
    """{'unit_w', 'mmio': Copy|None, 'msr': Copy|None}, or None when the RAPL unit is not readable (no root / no msr device)."""
    unit = msr.power_unit_w(root)
    if unit is None:
        return None
    return {"unit_w": unit, "mmio": read_mmio(root, unit), "msr": read_msr(root, unit)}


def mmio_caps(copies: dict | None, written_uw: dict[str, int]) -> list[tuple[str, float, float]]:
    """Enabled MMIO halves that are below what we wrote, as (name, MMIO W, our W). `written_uw`: 'pl1'/'pl2' -> µW."""
    if not copies or copies.get("mmio") is None:
        return []
    mm = copies["mmio"]
    out = []
    for name, half in (("pl1", mm.pl1), ("pl2", mm.pl2)):
        ours = written_uw.get(name)
        if ours is None or not half[1]:
            continue
        if half[0] * 1e6 < ours - 200_000:
            out.append((name, half[0], ours / 1e6))
    return out
