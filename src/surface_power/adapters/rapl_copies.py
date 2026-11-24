"""The copies of the PL1/PL2 limits, read only (spec outputs 3.3, deviation DV-8).

Windows writes PL1/PL2 to the MMIO copy (MCHBAR + 0x59A0); this daemon writes the MSR copy (0x610) through powercap. The lower
enabled copy is in force (measured on a Pro 10, linux-surface#2383; as a general rule [H]). This module only reads: it writes nothing.

MCHBAR is the 64-bit register at config offset 0x48 of the host bridge 00:00.0, bit 0 = enabled. The MMIO copy has the layout of
MSR 0x610: PL1 bits 14:0, enable bit 15, clamp bit 16; PL2 the same from bit 32; lock bit 63; the unit is MSR 0x606 bits 3:0.
Reading the config beyond 64 bytes needs CAP_SYS_ADMIN (the packaged units do not have it, plan B11), /dev/mem needs CAP_SYS_RAWIO and
lockdown `none`; when the MMIO copy cannot be read, the value is None together with the reason (spec kernel-mmio-daemon 8.3 E).
"""
import glob
import mmap
import os
import struct
from dataclasses import dataclass

from . import msr

MCHBAR_CONFIG = "sys/bus/pci/devices/0000:00:00.0/config"
MCHBAR_OFFSET = 0x48
MCHBAR_ADDR_MASK = ((1 << 42) - 1) & ~0x7FFF      # bits 41:15, as GENMASK_ULL(41, 15) in kernel/surface-rapl-mmio
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


@dataclass(frozen=True)
class ZoneCopy:
    """The MMIO copy as an `intel-rapl-mmio` zone shows it (spec kernel-mmio-daemon 8.3 C): PL1, PL2, the PL1 window and PL1
    enable; the zone shows no PL2 enable, clamp or lock bit [C, get_domain_enable]. Same interface as Copy; unknown bits are None."""
    pl1_w: float
    pl2_w: float
    pl1_enabled: bool
    window_s: float | None = None

    @property
    def pl1(self) -> tuple[float, bool, None]:
        return self.pl1_w, self.pl1_enabled, None

    @property
    def pl2(self) -> tuple[float, None, None]:
        return self.pl2_w, None, None

    lock = None

    def describe(self) -> str:
        w = f", window {self.window_s:g} s" if self.window_s is not None else ""
        return f"PL1 {self.pl1_w:g} W ({'on' if self.pl1_enabled else 'off'}), PL2 {self.pl2_w:g} W{w} (from the zone: no PL2 " \
               "enable, clamp or lock)"


def _num(path: str) -> int | None:
    try:
        with open(path) as f:
            v = f.read().strip()
    except OSError:
        return None
    return int(v) if v.isdigit() else None


def read_zone(zone: str) -> ZoneCopy | None:
    """PL1/PL2 of a powercap package zone (constraint 0 = long_term, 1 = short_term, as the RAPL core orders them)."""
    pl1, pl2, en = (_num(os.path.join(zone, n)) for n in ("constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "enabled"))
    if pl1 is None or pl2 is None or en is None:
        return None
    win = _num(os.path.join(zone, "constraint_0_time_window_us"))
    return ZoneCopy(pl1 / 1e6, pl2 / 1e6, bool(en), None if win is None else round(win / 1e6, 3))


def _mmio64(phys: int, root: str) -> int:
    """64 bits at a physical address through /dev/mem; raises OSError (lockdown, STRICT_DEVMEM, no capability)."""
    page = phys & ~(mmap.PAGESIZE - 1)
    fd = os.open(os.path.join(root, "dev/mem"), os.O_RDONLY | os.O_SYNC)
    try:
        m = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED, mmap.PROT_READ, offset=page)
        try:
            return struct.unpack("<Q", m[phys - page:phys - page + 8])[0]
        finally:
            m.close()
    except ValueError as e:
        raise OSError(str(e)) from e
    finally:
        os.close(fd)


def mchbar(root: str) -> tuple[int | None, str | None]:
    """(address, None), or (None, reason)."""
    try:
        with open(os.path.join(root, MCHBAR_CONFIG), "rb") as f:
            f.seek(MCHBAR_OFFSET)
            raw = f.read(8)
    except OSError as e:
        return None, f"PCI config of 00:00.0 not readable ({e.strerror or e})"
    if len(raw) != 8:
        # the kernel shows only the first 64 bytes of a PCI config without CAP_SYS_ADMIN and returns 0 bytes beyond, without an
        # error [C, drivers/pci/pci-sysfs.c pci_read_config]
        return None, "PCI config beyond 64 bytes needs CAP_SYS_ADMIN"
    v = struct.unpack("<Q", raw)[0]
    if not v & 1:
        return None, "MCHBAR not enabled"
    return v & MCHBAR_ADDR_MASK, None


def has_proc_thermal(root: str) -> bool:
    """The Processor Thermal PCI device 00:04.0 (driver processor_thermal_device_pci, which provides `intel-rapl-mmio`)."""
    return os.path.isdir(os.path.join(root, PROC_THERMAL))


def has_rapl_mmio_zone(root: str) -> bool:
    return bool(glob.glob(os.path.join(root, "sys/class/powercap/intel-rapl-mmio:*")))


def read_mmio(root: str, unit_w: float) -> tuple[Copy | None, str | None]:
    """(copy, None), or (None, reason)."""
    base, why = mchbar(root)
    if base is None:
        return None, why
    try:
        v = _mmio64(base + MMIO_PL_OFFSET, root)
    except OSError as e:
        return None, f"/dev/mem not readable ({e.strerror or e})"
    return Copy(v, unit_w), None


MMIO_UNIT_OFFSET = 0x5938          # the MMIO copy of MSR 0x606 (units); SB3: 0x000a0e03 [M]


def time_unit(root: str) -> tuple[int | None, str]:
    """RAPL time unit (bits 19:16; 10 = 1/1024 s) and where it came from: MCHBAR + 0x5938 where readable, else MSR 0x606
    (spec kernel-mmio-daemon 4.3). (None, why) when neither is readable."""
    base, why = mchbar(root)
    if base is not None:
        try:
            return _mmio64(base + MMIO_UNIT_OFFSET, root) >> 16 & 0xF, "MCHBAR + 0x5938"
        except OSError:
            pass
    v = msr.read(root, msr.RAPL_POWER_UNIT)
    if v is not None:
        return v >> 16 & 0xF, "MSR 0x606"
    return None, "neither MCHBAR + 0x5938 nor MSR 0x606 readable"


def read_msr(root: str, unit_w: float) -> Copy | None:
    v = msr.read(root, MSR_PL)
    return None if v is None else Copy(v, unit_w)


def read_all(root: str, zone: str | None = None) -> dict | None:
    """{'unit_w', 'mmio': Copy|ZoneCopy|None, 'mmio_source': 'register'|'zone'|None, 'mmio_unreadable': reason|None,
    'msr': Copy|None}. The register where it is readable, else the MMIO zone `zone` (spec kernel-mmio-daemon 8.3 C). None when
    neither the RAPL unit (no root / no msr device) nor a zone is readable."""
    unit = msr.power_unit_w(root)
    zc = read_zone(zone) if zone else None
    if unit is None and zc is None:
        return None
    mm, why = read_mmio(root, unit) if unit is not None else (None, "MSR 0x606 (power unit) not readable")
    src = "register" if mm is not None else None
    if mm is None and zc is not None:
        mm, why, src = zc, None, "zone"
    return {"unit_w": unit, "mmio": mm, "mmio_source": src, "mmio_unreadable": why,
            "msr": read_msr(root, unit) if unit is not None else None}


def mmio_caps(copies: dict | None, written_uw: dict[str, int], which: str = "mmio") -> list[tuple[str, float, float]]:
    """Enabled halves of the copy `which` ("mmio" or "msr") that are below what we wrote, as (name, copy W, our W).
    `written_uw`: 'pl1'/'pl2' -> µW. A half whose enable is unknown (None, from a zone) is not reported."""
    if not copies or copies.get(which) is None:
        return []
    mm = copies[which]
    out = []
    for name, half in (("pl1", mm.pl1), ("pl2", mm.pl2)):
        ours = written_uw.get(name)
        if ours is None or not half[1]:
            continue
        if half[0] * 1e6 < ours - 200_000:
            out.append((name, half[0], ours / 1e6))
    return out
