"""S3 and S4: the module surface_rapl_mmio on this kernel (spec tester-kit.md 6.2, kernel-mmio-rapl.md 6.1, KM-A6, KM-A9).

Both load it, look, and unload it; neither writes a limit. S3 loads it read only, S4 with write permission and writes nothing.
A stage passes only if every check holds; anything unexpected is "failed" and locks the machine for that stage.
"""
import os
import re
import shutil
import time

from ...adapters import rapl_copies
from . import MCHBAR_BY_PLATFORM, MODULE_SRC, ZONE, read, read_int, sh, signing, system
from .undo import unload_module

NAME = "surface_rapl_mmio"
SOURCES = ("surface_rapl_mmio.c", "Kbuild", "Makefile")
# SKUs in the module's DMI table: no unsafe_platform needed
IN_MODULE_TABLE = {"Surface_Book_3_1899", "Surface_Pro_8_1983", "Surface_Pro_10_for_Business_2079"}
LOCK_BITS = (1 << 31) | (1 << 63)
PL1_FIELD, PL2_FIELD = 0x7FFF, 0x7FFF << 32
ENABLE_BITS = (3 << 15) | (3 << 47)            # enable and clamp of PL1 and PL2
PLAUSIBLE_W = (3, 400)                         # [D]
# Taint bits that are bad news when they appear during a test. Our own module sets O (out of tree), E (unsigned) and S
# (unsafe_platform, allow_write); the msr driver sets S too.
BAD_TAINT = {4: "machine check", 5: "bad page", 7: "oops", 9: "kernel warning", 14: "soft lockup"}
# As the kernel writes them, case included: a lower-case "warning:" of some driver (ACPI Warning: ...) is not a kernel warning.
BAD_LINE = re.compile(r"WARNING: (CPU:|at |\S+:\d+)|\bBUG: |\bOops\b|general protection fault|Call Trace:|soft lockup|"
                      r"blocked for more than \d+ seconds|\[Hardware Error\]|Machine check events logged|Machine Check Exception")
HARMLESS = re.compile(r"temperature above threshold|temperature/speed normal|package power limit", re.IGNORECASE)
HEADERS = {"debian": "sudo apt install linux-headers-surface build-essential",
           "arch": "sudo pacman -S linux-surface-headers base-devel",
           "fedora": "sudo dnf install kernel-surface-devel make gcc"}


def build_dir(release: str) -> str:
    return f"/lib/modules/{release}/build"


def prerequisites(root: str, release: str, compiler: str | None) -> list[str]:
    """What is missing to build the module, each with what to do. Empty: it can be built."""
    missing = []
    if not os.path.isdir(os.path.join(root, build_dir(release).lstrip("/"))):
        how = HEADERS.get(system.distro_family(root), "install the headers of your linux-surface kernel")
        missing.append(f"kernel headers for {release}: {how}")
    tools = ["make"] + (["clang", "ld.lld"] if compiler == "clang" else ["gcc"])
    absent = [t for t in tools if not shutil.which(t)]
    if absent:
        missing.append(f"build tools {', '.join(absent)}: " + HEADERS.get(system.distro_family(root), "install them"))
    return missing


class Register:
    """The kit's own read of the MMIO limit register, independent of the module."""

    def __init__(self, raw: int, unit: int, base: int):
        self.raw, self.unit, self.base = raw, unit, base
        self.copy = rapl_copies.Copy(raw, 1.0 / (1 << (unit & 0xF)))

    def window_us(self) -> int:
        """PL1 window: bits 23:17, (1 + F/4) * 2^Y time units; the unit as the kernel rounds it [C, intel_rapl_common]."""
        field = self.raw >> 17 & 0x7F
        y, f = field & 0x1F, field >> 5
        time_unit_us = 1000000 // (1 << (self.unit >> 16 & 0xF))
        return (1 << y) * (4 + f) * time_unit_us // 4

    @classmethod
    def read(cls, root: str, platform: str) -> tuple["Register | None", str | None]:
        base, why = rapl_copies.mchbar(root)
        if base is None:
            return None, why
        if base != MCHBAR_BY_PLATFORM.get(platform):
            return None, f"MCHBAR {base:#x} differs from the constant of {platform}"
        try:
            raw = rapl_copies._mmio64(base + rapl_copies.MMIO_PL_OFFSET, root)
            unit = rapl_copies._mmio64(base + rapl_copies.MMIO_UNIT_OFFSET, root) & 0xFFFFFFFF
        except OSError as e:
            return None, f"/dev/mem not readable ({e.strerror or e})"
        return cls(raw, unit, base), None

    def implausible(self) -> str | None:
        """Why the module must not be loaded on what was read."""
        if self.unit in (0, 0xFFFFFFFF) or not self.unit & 0xF:
            return f"the unit register reads {self.unit:#010x}"
        if self.raw & LOCK_BITS:
            return "the firmware has locked the limit register"
        for name, half in (("PL1", self.copy.pl1), ("PL2", self.copy.pl2)):
            if not PLAUSIBLE_W[0] <= half[0] <= PLAUSIBLE_W[1]:
                return f"{name} reads {half[0]:g} W, outside {PLAUSIBLE_W[0]} to {PLAUSIBLE_W[1]} W"
        return None


def zone_values(root: str) -> dict | None:
    if not os.path.isdir(os.path.join(root, ZONE)):
        return None
    keys = ("enabled", "constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_0_time_window_us")
    out = {k: read_int(root, f"{ZONE}/{k}") for k in keys}
    return None if any(v is None for v in out.values()) else out


def compare(zone: dict, reg: Register) -> list[str]:
    """The differences between what the module's zone says and the register."""
    want = {"constraint_0_power_limit_uw": round(reg.copy.pl1[0] * 1e6), "constraint_1_power_limit_uw": round(reg.copy.pl2[0] * 1e6),
            "enabled": int(reg.copy.pl1[1]), "constraint_0_time_window_us": reg.window_us()}
    return [f"{k}: zone {zone[k]}, register {v}" for k, v in want.items() if abs(zone[k] - v) > (0 if k == "enabled" else 1)]


class Health:
    """What the kernel says since a point in time: new bad taint bits and warning lines."""

    def __init__(self, root: str = "/", wall=time.time):
        self.root = root
        self.since = int(wall())
        self.taint = read_int(root, "proc/sys/kernel/tainted") or 0

    def problems(self) -> list[str]:
        now = read_int(self.root, "proc/sys/kernel/tainted") or 0
        out = [f"new kernel taint: {what}" for bit, what in BAD_TAINT.items() if now >> bit & 1 and not self.taint >> bit & 1]
        code, text = sh.run(["journalctl", "-k", "--since", f"@{self.since}", "--no-pager", "-o", "cat"])
        if code == 0:
            out += [f"kernel log: {ln[:200]}" for ln in text.splitlines() if BAD_LINE.search(ln) and not HARMLESS.search(ln)][:5]
        return out


def module_log(lines: int = 60) -> list[str]:
    """The kernel's lines about the module, and its own line when it refuses one ("is rejected", signing.c)."""
    _, text = sh.run(["journalctl", "-k", "-b", "--no-pager", "-o", "cat", "-n", str(lines)])
    return [ln for ln in text.splitlines() if NAME in ln or "intel_rapl" in ln or "is rejected" in ln][-12:]


def loaded(root: str) -> bool:
    return os.path.isdir(os.path.join(root, "sys/module", NAME))


def build(tmp: str, release: str, compiler: str | None, sign_as: signing.Signing) -> tuple[str | None, str]:
    """(path of the signed module, text) or (None, why)."""
    for f in SOURCES:
        shutil.copy(os.path.join(MODULE_SRC, f), tmp)
    cmd = ["make", "-C", build_dir(release), f"M={tmp}", "modules"] + (["LLVM=1"] if compiler == "clang" else [])
    code, text = sh.run(cmd, timeout=600)
    ko = os.path.join(tmp, f"{NAME}.ko")
    if code != 0 or not os.path.exists(ko):
        return None, "build failed:\n" + text[-1500:]
    ok, how = signing.sign(sign_as, build_dir(release), ko)
    return (ko, f"built for {release}; {how}") if ok else (None, how)


def parameters(platform: str, sku: str, allow_write: bool) -> list[str]:
    return [f"allow_write={int(allow_write)}"] + ([] if sku in IN_MODULE_TABLE else [f"unsafe_platform={platform}"])


def load(undo, ko: str, params: list[str]) -> tuple[list[str] | None, str]:
    """(the pending unload command, "") or (None, why it was refused). The unload is entered before the load."""
    unload = undo.push(unload_module(NAME))
    code, text = sh.run(["insmod", ko] + params)
    if code != 0:
        undo.drop(unload)
        return None, text
    return unload, ""


def test(root: str, undo, ko: str, platform: str, sku: str, allow_write: bool, sleep=None) -> dict:
    """S3 (allow_write False) or S4 (True). {"status", "summary", "details"}."""
    sleep = sleep or time.sleep
    before, why = Register.read(root, platform)
    if before is None or before.implausible():
        return {"status": "skipped", "summary": f"not loaded: {why or before.implausible()}.", "details": {}}
    health = Health(root)
    params = parameters(platform, sku, allow_write)
    details: dict = {"parameters": params, "register_before": f"{before.raw:#018x}", "unit": f"{before.unit:#010x}"}
    unload, refused = load(undo, ko, params)
    if unload is None:
        details.update(insmod=refused, kernel=module_log())
        problems = health.problems()
        if problems:
            return {"status": "failed", "summary": "the kernel reported a problem at insmod: " + problems[0], "details": details}
        return {"status": "skipped", "summary": f"insmod refused the module ({refused}); nothing was changed.", "details": details}
    problems = []
    try:
        zone = zone_values(root)
        details["zone"] = zone
        reg, why = Register.read(root, platform)
        if zone is None or reg is None:
            problems.append("the zone or the register could not be read after the load" + (f" ({why})" if why else ""))
        else:
            if reg.raw != before.raw:
                problems.append(f"the register changed at the load: {before.raw:#018x} -> {reg.raw:#018x}")
            if not allow_write:                    # S3 is the stage that compares and reads the energy counter
                problems += compare(zone, reg)
                first = read_int(root, f"{ZONE}/energy_uj")
                sleep(2)
                second = read_int(root, f"{ZONE}/energy_uj")
                details["energy_uj"] = [first, second]
                if first is None or second is None or first == second:
                    problems.append("the zone's energy counter does not count")
            sleep(3)
            later, _ = Register.read(root, platform)
            if later is not None and later.raw != reg.raw:
                problems.append(f"the register changed while the module was loaded: {reg.raw:#018x} -> {later.raw:#018x}")
        details["kernel"] = module_log()
    finally:
        code, text = undo.run_one(unload) if unload in undo.pending else (0, "")
    if code != 0:
        problems.append(f"the module could not be unloaded: {text}")
    after, why = Register.read(root, platform)
    if after is None:
        problems.append(f"the register could not be read after the unload ({why})")
    else:
        details["register_after"] = f"{after.raw:#018x}"
        if after.raw != before.raw:
            problems.append(f"the register is not what it was before the load: {before.raw:#018x} -> {after.raw:#018x}")
    problems += health.problems()
    if problems:
        details["problems"] = problems
        return {"status": "failed", "summary": "; ".join(problems[:3]), "details": details}
    what = ("loaded with write permission, nothing written; the register is bit-identical after the unload" if allow_write else
            "the zone equals the register (PL1, PL2, enable, window), the energy counter counts, the register is bit-identical "
            "after the unload")
    return {"status": "passed", "summary": what + ".", "details": details}
