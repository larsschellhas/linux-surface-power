"""Model-specific registers through /dev/cpu/<n>/msr (spec outputs 4). Needs root and kernel lockdown `none`.

The register is read and written as 8 bytes at the offset of its number. In tests the device file is a regular file in a fake
root, which supports the same positioned reads and writes.
"""
import os
import struct

RAPL_POWER_UNIT = 0x606
PL3_CONTROL = 0x615
PL3_LIMIT_MASK = 0x1FFF      # bits 0-12: the field Windows writes (descriptor mask 0x1fff in the Windows CPU client [C]); 13-14 stay as read
PL3_ENABLE = 1 << 15


def path(root: str, cpu: int = 0) -> str:
    return os.path.join(root, "dev/cpu", str(cpu), "msr")


def available(root: str) -> bool:
    """The device exists, the kernel allows raw MSR writes, and we may open it for writing."""
    try:
        with open(os.path.join(root, "sys/kernel/security/lockdown")) as f:
            lockdown = f.read()
    except OSError:
        lockdown = "[none]"                      # no lockdown support in the kernel
    p = path(root)
    return "[none]" in lockdown and os.path.exists(p) and os.access(p, os.R_OK | os.W_OK)


def read(root: str, reg: int, cpu: int = 0) -> int | None:
    return read_dev(path(root, cpu), reg)


def write(root: str, reg: int, value: int, cpu: int = 0) -> bool:
    return write_dev(path(root, cpu), reg, value)


def power_unit_w(root: str) -> float | None:
    """Watts per RAPL power unit: 1 / 2^PU, PU = bits 0-3 of MSR 0x606."""
    v = read(root, RAPL_POWER_UNIT)
    return None if v is None else 1.0 / (1 << (v & 0xF))


# Snapshot keys for MSRs, next to the powercap file paths (spec supervisor 3): "msr:<device path>@<register>".
KEY_PREFIX = "msr:"


def key(root: str, reg: int, cpu: int = 0) -> str:
    return f"{KEY_PREFIX}{path(root, cpu)}@{reg:#x}"


def parse_key(k: str) -> tuple[str, int] | None:
    """(device path, register) of a snapshot key, or None for a plain file path."""
    if not k.startswith(KEY_PREFIX):
        return None
    dev, reg = k[len(KEY_PREFIX):].rsplit("@", 1)
    return dev, int(reg, 16)


def read_dev(dev: str, reg: int) -> int | None:
    """Like read(), for a device path from a snapshot key."""
    try:
        fd = os.open(dev, os.O_RDONLY)
    except OSError:
        return None
    try:
        data = os.pread(fd, 8, reg)
        return struct.unpack("<Q", data)[0] if len(data) == 8 else None
    except OSError:
        return None
    finally:
        os.close(fd)


def write_dev(dev: str, reg: int, value: int) -> bool:
    try:
        fd = os.open(dev, os.O_WRONLY)
    except OSError:
        return False
    try:
        return os.pwrite(fd, struct.pack("<Q", value), reg) == 8
    except OSError:
        return False
    finally:
        os.close(fd)
