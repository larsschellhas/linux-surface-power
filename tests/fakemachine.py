"""A simulated machine for the tester kit: the commands it runs, the module, the MMIO register, services and the load.

Nothing here runs a command on the host. The register lives in the fake /dev/mem and is read through the kit's real code.
"""
import os
import shutil
import struct

import helpers  # noqa: F401  (puts src on the path)
from fakesys import FakeSys, MCHBAR, MMIO_ZONE, RAPL_UNITS

from surface_power.tools import check
from surface_power.tools.kit import load, module, sh, write

MSR_PL = (61 * 8 << 32 | 1 << 47 | 30 * 8 | 1 << 15 | 1 << 16)      # SB3 under no daemon: 30 W and 61 W, both on
FIRMWARE_OFF = 0x000001E800DD00C8                                    # SB3's MMIO copy: 25 W and 61 W, switched off [M]
ENABLED_28 = 0x0000811800DD80E0                                      # a Pro 10's copy: 28 W and 35 W, switched on [M, tester]
HOLD_W = 13.5                                                        # what else holds the SB3 without the daemon


class FakeMachine:
    def __init__(self, register: int = FIRMWARE_OFF, services=("power-profiles-daemon", "surface-powerd"), hold_w: float | None = HOLD_W):
        self.fs = FakeSys()
        self.root = self.fs.root
        self.register_at_boot = register
        self.hold_w = hold_w
        # the fake msr file is byte-addressed: only registers whose 8 bytes do not overlap may be set
        self.msr_values = {0x606: RAPL_UNITS, 0x610: MSR_PL, 0x65C: 0, 0x17: 1}
        self.fs.add_msr(self.msr_values)
        self.fs.add_mmio_register(register)
        self.fs.write("proc/cpuinfo", "processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: Fake CPU\n")
        self.fs.write("proc/version", "Linux version 7.3.6-1-surface (builder@somewhere) (gcc 13.3) #1")
        self.fs.write("proc/sys/kernel/tainted", 0)
        self.fs.write("proc/uptime", "100.0 50.0")
        self.fs.write("etc/os-release", 'ID=zorin\nID_LIKE="ubuntu debian"\nPRETTY_NAME="Zorin OS 18"\n')
        self.fs.write("sys/module/module/parameters/sig_enforce", "N")
        os.makedirs(self.fs.path("sys/bus/platform/devices/MSHW0117:00"))
        os.makedirs(self.fs.path("sys/class/power_supply/BAT1/dptf"))
        os.makedirs(self.fs.path("lib/modules/7.3.6-1-surface/build"))
        self.units = {u: "active" for u in services}
        self.masked: set[str] = set()
        self.commands: list[list[str]] = []
        self.journal: list[str] = []
        self.loaded_params: list[str] | None = None
        # knobs for the failure cases
        self.unload_leaves = None            # register value an unload leaves behind instead of restoring
        self.insmod_error = None
        self.write_touches = 0               # extra bits every zone write flips
        self.write_has_no_effect = False
        self.on_load_run = None              # called at the start of every load run
        self.real_sampler = False            # take the sensor readings of a load run from the kit's real sampler

    # --- the register ---
    def reg(self) -> int:
        return self.fs.mmio_register()

    def set_reg(self, value: int) -> None:
        with open(self.fs.path("dev/mem"), "r+b") as f:
            f.seek(MCHBAR + 0x59A0)
            f.write(struct.pack("<Q", value))

    def _zone_from_register(self) -> None:
        r = module.Register(self.reg(), RAPL_UNITS, MCHBAR)
        self.fs.write(f"{MMIO_ZONE}/name", "package-0")
        self.fs.write(f"{MMIO_ZONE}/enabled", int(r.copy.pl1[1]))
        self.fs.write(f"{MMIO_ZONE}/constraint_0_power_limit_uw", round(r.copy.pl1[0] * 1e6))
        self.fs.write(f"{MMIO_ZONE}/constraint_1_power_limit_uw", round(r.copy.pl2[0] * 1e6))
        self.fs.write(f"{MMIO_ZONE}/constraint_0_time_window_us", r.window_us())
        if not os.path.exists(self.fs.path(f"{MMIO_ZONE}/energy_uj")):
            self.fs.write(f"{MMIO_ZONE}/energy_uj", 1000)

    def tick(self, seconds: float) -> None:
        """Instead of sleeping: the energy counter moves."""
        p = f"{MMIO_ZONE}/energy_uj"
        if os.path.exists(self.fs.path(p)):
            self.fs.write(p, int(self.fs.read(p)) + int(seconds * 1e6))

    def write_zone(self, root: str, name: str, value: int) -> None:
        raw = self.reg()
        if name == "enabled":
            bits = (3 << 15) | (3 << 47)
            raw = raw | bits if value else raw & ~bits
        else:
            field = int(value / 1e6 * 8)
            shift = 0 if name.startswith("constraint_0") else 32
            raw = raw & ~(0x7FFF << shift) | field << shift
        self.set_reg(raw ^ self.write_touches)
        self._zone_from_register()

    # --- commands ---
    def run(self, cmd: list[str], timeout: int = 120, **kw) -> tuple[int, str]:
        self.commands.append(cmd)
        if cmd[:2] == ["/bin/sh", "-c"]:
            script = cmd[2]
            if script.startswith("systemctl stop "):
                return self.run(["systemctl", "stop", script.split()[2]])
            if "rmmod surface_rapl_mmio" in script:
                return (0, "") if self.loaded_params is None else self.run(["rmmod", "surface_rapl_mmio"])
            raise AssertionError(f"unknown shell command {script}")
        name = os.path.basename(cmd[0])
        if name == "rm":
            shutil.rmtree(cmd[-1], ignore_errors=True)
            return 0, ""
        if name == "git":
            return 0, "abc123def456" if "rev-parse" in cmd else "" if "status" in cmd else "2000000000"
        if name == "systemctl":
            verb, unit = cmd[1], next((a for a in cmd[2:] if a.endswith(".service")), "").removesuffix(".service")
            if verb == "show":
                if "CPUUsageNSec" in cmd:
                    return 0, "CPUUsageNSec=1000000000\nMemoryPeak=14000000"
                if unit not in self.units:
                    return 0, "LoadState=not-found\nActiveState=inactive\nUnitFileState="
                return 0, f"LoadState=loaded\nActiveState={self.units[unit]}\nUnitFileState={'masked-runtime' if unit in self.masked else 'enabled'}"
            if verb == "stop" and unit in self.units:
                if unit == "surface-powerd" and self.units[unit] == "active":        # the daemon hands PL4 back at its stop
                    self.fs.write("sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw", 0)
                self.units[unit] = "inactive"
            if verb == "start" and unit in self.units:
                if unit in self.masked:
                    return 1, f"Unit {unit}.service is masked."
                self.units[unit] = "active"
                if unit == "surface-powerd":
                    self.fs.write("sys/class/powercap/intel-rapl:0/constraint_2_power_limit_uw", 111375000)
            if verb == "mask":
                self.masked.add(unit)
            if verb == "unmask":
                self.masked.discard(unit)
            return 0, ""
        if name == "make":
            tmp = next(a for a in cmd if a.startswith("M="))[2:]
            open(os.path.join(tmp, "surface_rapl_mmio.ko"), "w").close()
            return 0, ""
        if name == "insmod":
            if self.insmod_error:
                self.journal.append("Loading of module with unavailable key is rejected")
                return 1, self.insmod_error
            self.loaded_params, self.saved = cmd[2:], self.reg()
            os.makedirs(self.fs.path("sys/module/surface_rapl_mmio"))
            self._zone_from_register()
            self.journal.append("surface_rapl_mmio: intel-rapl-mmio zone created")
            return 0, ""
        if name == "rmmod":
            if self.loaded_params is None:
                return 1, "not loaded"
            self.set_reg(self.saved if self.unload_leaves is None else self.unload_leaves)
            shutil.rmtree(self.fs.path("sys/module/surface_rapl_mmio"))
            shutil.rmtree(self.fs.path(MMIO_ZONE))
            self.loaded_params = None
            return 0, ""
        if name == "journalctl":
            if "-u" in cmd:
                return 0, "dry-run started\nstopped"
            return 0, "\n".join(self.journal)
        if name == "modprobe" and cmd[-1] == "msr":
            if "-r" in cmd:
                os.remove(self.fs.path("dev/cpu/0/msr"))
            else:
                self.fs.add_msr(self.msr_values)
            return 0, ""
        if name == "mokutil":
            return 1, "MOK.der is already enrolled"
        return 0, ""

    # --- the load: the package settles at the lowest limit that is switched on, or at what else holds it ---
    def plateau_w(self) -> float:
        limits = [30.0]
        r = module.Register(self.reg(), RAPL_UNITS, MCHBAR)
        if r.copy.pl1[1] and not self.write_has_no_effect:
            limits.append(r.copy.pl1[0])
        if self.hold_w:
            limits.append(self.hold_w)
        return min(limits)

    def run_phases(self, phases, sampler, loadgen, skin_refs=(), clock=None, sleep=None, on_phase=None, on_sample=None) -> dict:
        samples, t = [], 0.0
        for phase, seconds, loaded in phases:
            started = t
            if loaded and self.on_load_run:
                self.on_load_run()
            for _ in range(seconds):
                t += 1
                read = sampler.sample() if self.real_sampler else {}
                samples.append({**read, "t": t, "phase": phase, "pkg_w": self.plateau_w() if loaded else 3.0, "cpu_c": 70.0, "ac": True})
                if on_sample:
                    on_sample(phase, samples[-1], t - started, seconds)
        return {"samples": samples, "aborted": None}

    def patches(self, work: str, real_report: bool = False):
        """`real_report`: the kit's real report and the daemon's own view run on the fake sysfs (they need a blob there)."""
        from unittest import mock
        replaced = [] if real_report else [
            mock.patch.object(check.report, "main", lambda argv, **kw: print("report text") or 0),
            mock.patch.object(check.report, "daemon_view", lambda root, blob: (None, "no blob in this simulation"))]
        return replaced + [mock.patch.object(sh, "run", self.run), mock.patch.object(load, "run_phases", self.run_phases),
                mock.patch.object(write, "_write_zone", self.write_zone), mock.patch.object(check, "WORK", os.path.join(os.path.dirname(work), "surface-power-check")),
                mock.patch.object(module.time, "sleep", self.tick), mock.patch.object(check.time, "sleep", self.tick),
                mock.patch.object(module.shutil, "which", lambda t: f"/usr/bin/{t}"),
                mock.patch.object(check.supervisor, "limit_writers",
                                  lambda root="/": sorted(u for u, s in self.units.items() if s == "active" and u != "surface-powerd"))]

    def cleanup(self):
        self.fs.cleanup()
