#!/usr/bin/env python3
"""Field test on a real device (acceptance criteria AC-L1 - AC-L3, AC-L5): runs surface-powerd in control mode under a defined load.

Sequence:
  1. Checks (root, AC state as requested), sets the platform profile, stops thermald.
  2. Starts surface-powerd from this checkout with --control and its own run/state directories in OUT.
  3. Cool-down: waits until the skin sensor is at most --start-skin °C (at most 15 min).
  4. Load for --load-min minutes (CPU: x265 encode; with --gpu also a CUDA filter + NVENC on the NVIDIA GPU), then --idle-min idle.
  5. Logs one row per second to OUT/field-test.csv. The daemon's own series and status are in OUT/state and OUT/run.
  6. Always, also on Ctrl-C or abort: stops the load and the daemon (which restores the firmware values),
     restores the platform profile and starts thermald again if it was running.

Safety stops on top of the daemon's guard: skin >= 56 °C, or CPU >= 105 °C for 15 s (above TjMax), or the daemon exits.

Usage (as root):  python3 scripts/field-test.py --profile performance --load-min 30 --idle-min 5 --out DIR [--gpu] [--battery]
"""
import argparse
import csv
import glob
import json
import os
import signal
import subprocess
import sys
import time

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RAPL = "/sys/class/powercap"
PROFILE = "/sys/firmware/acpi/platform_profile"

CPU_LOAD = ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60",
            "-c:v", "libx265", "-preset", "medium", "-x265-params", "log-level=error", "-f", "null", "-"]
GPU_LOAD = ["ffmpeg", "-nostdin", "-loglevel", "error", "-init_hw_device", "cuda=cu:0", "-filter_hw_device", "cu",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60",
            "-vf", "format=yuv420p,hwupload_cuda,bilateral_cuda=sigmaS=10:sigmaR=0.1:window_size=21,"
                   "bilateral_cuda=sigmaS=10:sigmaR=0.1:window_size=21",
            "-c:v", "hevc_nvenc", "-preset", "p7", "-f", "null", "-"]

SKIN_STOP_C, CPU_STOP_C, CPU_STOP_S = 56.0, 105.0, 15  # CPU: the hardware already holds 99-100 °C (TjMax) under load


def rd(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def wr(path: str, value: str) -> None:
    with open(path, "w") as f:
        f.write(value)


def hwmon(name: str, ssam_device: str | None = None) -> str | None:
    """hwmon directory by name; with `ssam_device`, only the one whose device is that SSAM node (SB3: SAM and KIP both
    register a `surface_thermal`)."""
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if rd(f"{d}/name") != name:
            continue
        if ssam_device and os.path.basename(os.path.realpath(f"{d}/device")) != ssam_device:
            continue
        return d
    return None


def ac_online() -> bool:
    return any(rd(f"{d}/online") == "1" for d in glob.glob("/sys/class/power_supply/*") if rd(f"{d}/type") == "Mains")


class Probe:
    """Reads the measured values for one CSV row. Power comes from RAPL energy counters (package = intel-rapl:0, system = psys intel-rapl:1)."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        thermal, core = hwmon("surface_thermal", "01:03:01:00:02"), hwmon("coretemp")   # display part (SAM)
        base = hwmon("surface_thermal", "01:03:02:00:02")                                 # base (KIP), if present
        self.skin_path = f"{thermal}/temp9_input" if thermal else None              # I_VTS1 (channel 509)
        self.base_path = f"{base}/temp9_input" if base else None                    # base I_VTS1 (channel 609)
        self.cpu_path = next((p[:-6] + "_input" for p in glob.glob(f"{core}/temp*_label") if rd(p) == "Package id 0"), None) if core else None
        if not self.skin_path:
            raise SystemExit("display skin sensor (surface_thermal on 01:03:01:00:02) not found")
        self.domains = {"pkg": f"{RAPL}/intel-rapl:0", "sys": f"{RAPL}/intel-rapl:1"}
        self.last = {k: (int(rd(f"{p}/energy_uj") or 0), time.monotonic()) for k, p in self.domains.items()}

    def _power_w(self, key: str) -> float | None:
        path = self.domains[key]
        e, t = int(rd(f"{path}/energy_uj") or 0), time.monotonic()
        e0, t0 = self.last[key]
        self.last[key] = (e, t)
        if e < e0:                                   # counter wrapped
            e += int(rd(f"{path}/max_energy_range_uj") or 0)
        return round((e - e0) / 1e6 / (t - t0), 2) if t > t0 else None

    def row(self) -> dict:
        skin = rd(self.skin_path) if self.skin_path else None
        cpu = rd(self.cpu_path) if self.cpu_path else None
        lim = lambda d, c: int(rd(f"{RAPL}/{d}/constraint_{c}_power_limit_uw") or 0) / 1e6
        base = rd(self.base_path) if self.base_path else None
        rs = rd(f"/sys/bus/pci/devices/0000:02:00.0/power/runtime_status")
        r = {"skin_c": int(skin) / 1000 if skin else None, "base_c": int(base) / 1000 if base else None, "gpu_rpm": rs,
             "cpu_c": int(cpu) / 1000 if cpu else None,
             "pkg_w": self._power_w("pkg"), "sys_w": self._power_w("sys"),
             "pl1_w": lim("intel-rapl:0", 0), "pl2_w": lim("intel-rapl:0", 1),
             "psys_pl1_w": lim("intel-rapl:1", 0), "psys_pl2_w": lim("intel-rapl:1", 1), "ac": int(ac_online())}
        try:
            with open(os.path.join(self.run_dir, "status.json")) as f:
                st = json.load(f)
            r["state"] = st.get("state")
            r["slider"] = st.get("slider", {}).get("state")
            for c in st.get("controllers", []):
                r[f"ctl{c['line']}"] = f"{c['state']}/set{c['set']}/u{c['u_w']}"
            g = st.get("dgpu")
            if g:   # from the daemon's own samples; the script itself never queries the GPU (that would keep it awake)
                r.update(dgpu_target_w=g["target_w"], dgpu_cap_mhz=g["cap_mhz"], dgpu_power_w=g["power_w"],
                         dgpu_clock_mhz=g["clock_mhz"], dgpu_error=g["error"])
        except (OSError, ValueError):
            r["state"] = None
        return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True, choices=["low-power", "balanced", "balanced-performance", "performance"])
    ap.add_argument("--load-min", type=float, default=30)
    ap.add_argument("--idle-min", type=float, default=5)
    ap.add_argument("--start-skin", type=float, default=42.0, help="start the load once I_VTS1 is at most this (°C)")
    ap.add_argument("--gpu", action="store_true", help="add NVIDIA GPU load (AC-L3)")
    ap.add_argument("--battery", action="store_true", help="expect to run on battery (AC-L2)")
    ap.add_argument("--dgpu-clock-cap", action="store_true", help="enable the dGPU clock-cap stand-in (DO-1)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("needs root")
    if ac_online() == a.battery:
        sys.exit("unplug the charger first" if a.battery else "plug in the charger first")
    os.makedirs(a.out, exist_ok=True)
    run_dir, state_dir = os.path.join(a.out, "run"), os.path.join(a.out, "state")

    orig_profile = rd(PROFILE)
    thermald_was_active = subprocess.run(["systemctl", "is-active", "--quiet", "thermald"]).returncode == 0
    loads: list[subprocess.Popen] = []
    daemon = None
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))

    def say(text: str) -> None:
        print(f"{time.strftime('%H:%M:%S')}  {text}", flush=True)

    try:
        wr(PROFILE, a.profile)
        if thermald_was_active:
            subprocess.run(["systemctl", "stop", "thermald"], check=True)
        env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"))
        extra = ["--dgpu-clock-cap"] if a.dgpu_clock_cap else []
        daemon = subprocess.Popen([sys.executable, "-m", "surface_power.daemon", "--control", "--run-dir", run_dir,
                                   "--state-dir", state_dir, *extra], env=env,
                                  stdout=open(os.path.join(a.out, "daemon.log"), "w"), stderr=subprocess.STDOUT)
        say(f"profile {a.profile}, AC {ac_online()}, thermald stopped: {thermald_was_active}, daemon pid {daemon.pid}")
        time.sleep(3)
        probe = Probe(run_dir)

        phase, phase_start = "cooldown", time.monotonic()
        hot_since = None
        with open(os.path.join(a.out, "field-test.csv"), "w", newline="") as f:
            w = None
            t0 = time.monotonic()
            while not stop["flag"]:
                now = time.monotonic()
                r = probe.row()
                # Phase changes.
                if phase == "cooldown" and ((r["skin_c"] or 99) <= a.start_skin or now - phase_start > 900):
                    phase, phase_start = "load", now
                    loads.append(subprocess.Popen(CPU_LOAD, stdout=subprocess.DEVNULL))
                    if a.gpu:
                        loads.append(subprocess.Popen(GPU_LOAD, stdout=subprocess.DEVNULL))
                    say(f"load starts (skin {r['skin_c']} °C)")
                elif phase == "load" and now - phase_start >= a.load_min * 60:
                    for p in loads:
                        p.terminate()
                    loads.clear()
                    phase, phase_start = "idle", now
                    say("load ends")
                elif phase == "idle" and now - phase_start >= a.idle_min * 60:
                    say("done")
                    break
                # Safety stops.
                if daemon.poll() is not None:
                    say(f"ABORT: daemon exited with {daemon.returncode}")
                    break
                if (r["skin_c"] or 0) >= SKIN_STOP_C:
                    say(f"ABORT: skin {r['skin_c']} °C")
                    break
                hot_since = (hot_since or now) if (r["cpu_c"] or 0) >= CPU_STOP_C else None
                if hot_since and now - hot_since >= CPU_STOP_S:
                    say(f"ABORT: CPU {r['cpu_c']} °C for {CPU_STOP_S} s")
                    break
                # Row.
                r = {"t_s": round(now - t0, 1), "phase": phase, **r}
                if w is None:
                    w = csv.DictWriter(f, fieldnames=list(r), extrasaction="ignore")
                    w.writeheader()
                w.writerow(r)
                f.flush()
                if int(now - t0) % 30 == 0:
                    say(f"{phase:8} skin {r['skin_c']} °C, cpu {r['cpu_c']} °C, PL1 {r['pl1_w']} W, pkg {r['pkg_w']} W, "
                        f"sys {r['sys_w']} W, state {r['state']}")
                time.sleep(1 - (time.monotonic() % 1))
    finally:
        for p in loads:
            p.terminate()
        if daemon and daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(20)
            except subprocess.TimeoutExpired:
                daemon.kill()
        if orig_profile:
            wr(PROFILE, orig_profile)
        if thermald_was_active:
            subprocess.run(["systemctl", "start", "thermald"])
        say(f"restored: profile {rd(PROFILE)}, PL1 {int(rd(f'{RAPL}/intel-rapl:0/constraint_0_power_limit_uw') or 0) / 1e6} W, "
            f"psys {int(rd(f'{RAPL}/intel-rapl:1/constraint_0_power_limit_uw') or 0) / 1e6} W, thermald restarted: {thermald_was_active}")
        os.system(f"chown -R --reference={REPO} {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
