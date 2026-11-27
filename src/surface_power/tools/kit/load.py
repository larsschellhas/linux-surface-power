"""S2: the CPU load, the samples and the abort (spec tester-kit.md 6.4). Writes nothing.

Every load process ends as soon as the kit that started it is gone, and at its deadline at the latest, so a killed kit leaves no
load behind. The abort uses the daemon's own
emergency rules (skin sensor 60 °C or CPU package 105 °C for 10 s, spec supervisor 4).
"""
import glob
import os
import statistics
import struct
import subprocess
import sys
import time

from ... import supervisor
from ...adapters import inputs as inp
from ...adapters import msr, rapl_copies
from ...loader.model import ChannelRef
from . import read, read_int
from .. import sensorprobe
from .evidence_ec import TC_FAN

# (name, seconds, loaded) [numbers H, to be calibrated on the SB3]
PHASES = (("idle", 60, False), ("load", 150, True), ("cooldown", 60, False))
PLATEAU_S = 60                       # the end of the load phase that is averaged
MATCH_W = 1.5                        # a plateau this close to a limit is held by it [H]
# One busy process per CPU. SHA-512 over 1 MiB runs the vector units and needs no extra package; what power it draws on which CPU
# is not known yet [H].
# Arguments: seconds, and the process that started it. Once that one is gone the system gives the worker another parent.
WORKER = ("import hashlib, os, sys, time\n"
          "end, parent = time.monotonic() + float(sys.argv[1]), int(sys.argv[2])\n"
          "block = bytearray(1 << 20)\n"
          "while time.monotonic() < end and os.getppid() == parent:\n"
          "    block[:64] = hashlib.sha512(block).digest()\n")
LOAD_NAME = "python sha512, one process per CPU"


class Load:
    def __init__(self):
        self.procs: list = []

    def start(self, seconds: float, cpus: int | None = None) -> None:
        for _ in range(cpus or os.cpu_count() or 1):
            self.procs.append(subprocess.Popen([sys.executable, "-I", "-c", WORKER, str(seconds), str(os.getpid())],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def stop(self) -> None:
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []


class Sampler:
    """One sample: package and platform power, CPU and SAM temperatures, fans, CPU frequency."""

    def __init__(self, root: str = "/", transport=None, probe_sensors: list[int] = (), clock=time.monotonic, probe_fan: bool = False):
        self.root, self.transport, self.clock = root, transport, clock
        self.probe_fan, self.fan_probed = probe_fan, None   # the fan, where the kernel has no reading and the command answered
        self.probe_sensors = list(probe_sensors)      # sensors from the probe's mask where the kernel has no node
        self.last_energy: dict[str, tuple[float, int]] = {}
        self.probed: dict[int, float] = {}
        self.count = 0
        self.zones = {}
        for d in sorted(glob.glob(os.path.join(root, "sys/class/powercap/intel-rapl:*"))):
            name = read(d, "name")
            if name == "package-0":
                self.zones["pkg_w"] = d
            elif name == "psys":
                self.zones["psys_w"] = d

    def _power(self, key: str, zone: str) -> float | None:
        energy = read_int(zone, "energy_uj")
        if energy is None:
            return None
        now = self.clock()
        before = self.last_energy.get(key)
        self.last_energy[key] = (now, energy)
        if before is None or now <= before[0]:
            return None
        delta = energy - before[1]
        if delta < 0:                                  # the counter wrapped
            delta += read_int(zone, "max_energy_range_uj") or 0
        return None if delta < 0 else round(delta / 1e6 / (now - before[0]), 2)

    def sample(self) -> dict:
        s: dict = {}
        for key, zone in self.zones.items():
            s[key] = self._power(key, zone)
        fans, freqs = [], []
        for d in sorted(glob.glob(os.path.join(self.root, "sys/class/hwmon/hwmon*"))):
            name = read(d, "name")
            if name == "coretemp":
                for lab in glob.glob(os.path.join(d, "temp*_label")):
                    if read("/", lab) == "Package id 0":
                        v = read_int("/", lab[:-len("_label")] + "_input")
                        if v is not None:
                            s["cpu_c"] = round(v / 1000, 1)
            elif name == "surface_thermal" and inp._ssam_target(d) in (1, None):
                for f in glob.glob(os.path.join(d, "temp*_input")):
                    v = read_int("/", f)
                    if v is not None:
                        s[f"sam{os.path.basename(f)[4:-len('_input')]}_c"] = round(v / 1000, 1)
            elif name == "surface_fan":
                for f in sorted(glob.glob(os.path.join(d, "fan*_input"))):
                    v = read_int("/", f)
                    if v is not None:
                        fans.append(v)
        if self.probe_sensors and self.transport is not None:
            if self.count % 5 == 0:                    # an EC request each: every 5 s, only sensors in the mask
                for n in self.probe_sensors:
                    raw = self.transport.read(sensorprobe.TC_TMP, sensorprobe.CID_TEMPERATURE, n)
                    if raw is not None and len(raw) >= 2:
                        self.probed[n] = round((struct.unpack("<H", raw[:2])[0] - 2731) / 10, 1)
            s.update({f"sam{n}_c": v for n, v in self.probed.items()})
        if self.probe_fan and self.transport is not None:
            if self.count % 5 == 0:
                raw = self.transport.read(TC_FAN, 0x01, 1)
                if raw is not None and len(raw) >= 2:
                    self.fan_probed = struct.unpack("<H", raw[:2])[0]
            if self.fan_probed is not None:
                fans.append(self.fan_probed)
        for f in glob.glob(os.path.join(self.root, "sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")):
            v = read_int("/", f)
            if v is not None:
                freqs.append(v)
        if fans:
            s["fan_rpm"] = max(fans)
        if freqs:
            s["freq_mhz"] = round(sum(freqs) / len(freqs) / 1000)
        s["ac"] = inp._ac_online(self.root)
        self.count += 1
        return s


def guard_values(sample: dict) -> dict:
    """The sample's temperatures as the daemon's channels (0.1 K)."""
    out = {}
    for key, v in sample.items():
        if key.startswith("sam") and key.endswith("_c"):
            out[ChannelRef(inp.TEMP, 500 + int(key[3:-2]))] = 2731 + v * 10
    if "cpu_c" in sample:
        out[ChannelRef(inp.TEMP, 102)] = 2731 + sample["cpu_c"] * 10
    return out


def run_phases(phases, sampler, load, skin_refs=(), clock=time.monotonic, sleep=time.sleep, on_phase=None, on_sample=None) -> dict:
    """The phases at 1 Hz. {"samples": [...], "aborted": reason or None}. The load is stopped on every way out."""
    guard = supervisor.Guard(list(skin_refs), ChannelRef(inp.TEMP, 102))
    samples, aborted, t0, blind = [], None, clock(), 0
    try:
        for name, seconds, loaded in phases:
            if on_phase:
                on_phase(name, seconds)
            if loaded:
                load.start(seconds + 15)               # its own deadline, a little after the phase
            start = clock()
            while clock() - start < seconds and aborted is None:
                sleep(1)
                s = sampler.sample()
                s.update(t=round(clock() - t0, 1), phase=name)
                samples.append(s)
                if on_sample:
                    on_sample(name, s, clock() - start, seconds)
                reason = guard.check(int(clock() * 1000), guard_values(s), False)
                blind = blind + 1 if loaded and "cpu_c" not in s else 0
                if blind >= 10:
                    reason = "the CPU temperature is not readable"
                if loaded and reason:
                    aborted = reason
            if loaded:
                load.stop()
            if aborted:
                break
    finally:
        load.stop()
    return {"samples": samples, "aborted": aborted}


def plateau(samples: list[dict], phase: str = "load", last_s: int = PLATEAU_S) -> dict | None:
    """Package power at the end of the loaded phase; "steady" if it hardly moves."""
    rows = [s for s in samples if s.get("phase") == phase and s.get("pkg_w") is not None]
    if not rows:
        return None
    end = rows[-1]["t"]
    rows = [s for s in rows if s["t"] > end - last_s]
    if len(rows) < 20:
        return None
    watts = [s["pkg_w"] for s in rows]
    mean, sd = statistics.fmean(watts), statistics.pstdev(watts)
    out = {"mean_w": round(mean, 2), "sd_w": round(sd, 2), "min_w": min(watts), "max_w": max(watts), "samples": len(watts),
           "steady": sd <= max(1.0, 0.07 * mean)}
    psys = [s["psys_w"] for s in rows if s.get("psys_w") is not None]
    if psys:
        out["psys_mean_w"] = round(statistics.fmean(psys), 2)
    cpu = [s["cpu_c"] for s in rows if "cpu_c" in s]
    if cpu:
        out["cpu_max_c"] = max(cpu)
    return out


def enabled_limits(root: str, mmio_raw: int | None) -> dict[str, float]:
    """The PL1 values that are switched on now: MSR copy, MMIO copy, platform."""
    out = {}
    unit = msr.power_unit_w(root)
    value = msr.read(root, rapl_copies.MSR_PL)
    if unit and value is not None:
        copy = rapl_copies.Copy(value, unit)
        if copy.pl1[1]:
            out["MSR PL1"] = copy.pl1[0]
    if unit and mmio_raw is not None:
        copy = rapl_copies.Copy(mmio_raw, unit)
        if copy.pl1[1]:
            out["MMIO PL1"] = copy.pl1[0]
    for d in glob.glob(os.path.join(root, "sys/class/powercap/intel-rapl:*")):
        if read(d, "name") == "psys" and read_int(d, "enabled") == 1:
            v = read_int(d, "constraint_0_power_limit_uw")
            if v:
                out["platform PL1"] = v / 1e6
    return out


def in_force(top: dict | None, limits: dict[str, float]) -> str:
    """Which switched-on limit the plateau sits on. Careful words: a match is not a proof."""
    if top is None:
        return "inconclusive: no plateau was measured"
    if not top["steady"]:
        return f"inconclusive: the package power did not settle ({top['mean_w']} W ± {top['sd_w']} W)"
    hits = [f"{name} {w:g} W" for name, w in limits.items() if name != "platform PL1" and abs(top["mean_w"] - w) <= MATCH_W]
    if "platform PL1" in limits and "psys_mean_w" in top and abs(top["psys_mean_w"] - limits["platform PL1"]) <= MATCH_W:
        hits.append(f"platform PL1 {limits['platform PL1']:g} W (platform power {top['psys_mean_w']} W)")
    if hits:
        return f"package power settles at {top['mean_w']} W, at " + " and ".join(hits)
    return (f"inconclusive: package power settles at {top['mean_w']} W, not at a switched-on limit ("
            + (", ".join(f"{n} {w:g} W" for n, w in limits.items()) or "none readable") + "): something else holds it, or the load is too light")
