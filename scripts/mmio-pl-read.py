#!/usr/bin/env python3
"""Read only: PL1/PL2 in the MMIO copy (MCHBAR + 0x59A0) next to MSR 0x610 and powercap. Run as root.

Windows writes PL1/PL2 (channels 51/52) to the MMIO copy, Linux writes the MSR copy through powercap; the lower one is in force
(measured on a Pro 10, linux-surface#2383). Shows both copies on this device (docs/specs/outputs.md 3.3). Writes nothing.
"""
import mmap
import os
import struct
import sys


def mchbar() -> int:
    # MCHBAR: host bridge 00:00.0, config offset 0x48, 64 bit, bit 0 = enabled (Intel client datasheets)
    with open("/sys/bus/pci/devices/0000:00:00.0/config", "rb") as f:
        f.seek(0x48)
        v = struct.unpack("<Q", f.read(8))[0]
    if not v & 1:
        sys.exit("MCHBAR not enabled")
    return v & ~0x7FFF & ((1 << 39) - 1)


def mmio64(phys: int) -> int:
    page = phys & ~(mmap.PAGESIZE - 1)
    fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
    try:
        m = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED, mmap.PROT_READ, offset=page)
        try:
            return struct.unpack("<Q", m[phys - page:phys - page + 8])[0]
        finally:
            m.close()
    finally:
        os.close(fd)


def msr(reg: int) -> int:
    with open("/dev/cpu/0/msr", "rb") as f:
        f.seek(reg)
        return struct.unpack("<Q", f.read(8))[0]


def decode(v: int, unit: float) -> str:
    def half(x):
        return f"{(x & 0x7FFF) * unit:6.2f} W, enable {x >> 15 & 1}, clamp {x >> 16 & 1}"
    return f"PL1 {half(v)} | PL2 {half(v >> 32)} | lock {v >> 63}"


if os.geteuid() != 0:
    sys.exit("run as root")
os.system("modprobe msr")
unit = 1 / (1 << (msr(0x606) & 0xF))
base = mchbar()
print(f"RAPL unit: {unit} W, MCHBAR 0x{base:x}")
print(f"MMIO  MCHBAR+0x59A0: 0x{mmio64(base + 0x59A0):016x}  {decode(mmio64(base + 0x59A0), unit)}")
print(f"MSR   0x610         : 0x{msr(0x610):016x}  {decode(msr(0x610), unit)}")
for c in (0, 1):
    p = f"/sys/class/powercap/intel-rapl:0/constraint_{c}_"
    with open(p + "name") as n, open(p + "power_limit_uw") as v:
        print(f"powercap {n.read().strip():10s}: {int(v.read()) / 1e6:6.2f} W")
