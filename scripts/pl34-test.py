#!/usr/bin/env python3
"""PL3 / PL4 check on an Intel Surface (run as root): can Linux write them, do they bind, and does the restore work.

Steps, each with a 6 s all-core load and the package / psys power sampled every 100 ms from RAPL energy counters:
  1. baseline with the firmware values;
  2. PL4 = 20 W through powercap `peak_power` (if present), then the blob's value, then the firmware value back;
  3. PL3 = 20 W in MSR 0x615 with the enable bit as found, then, only if it was off, once with the enable bit set;
     then the firmware register back.
Everything is restored in a `finally` block, also on Ctrl-C. Nothing else is changed. Results go to OUT/pl34.json and stdout.
Usage: sudo python3 -I scripts/pl34-test.py OUT [--pl4-blob-w 107] [--pl3-blob-w 100]
"""
import argparse
import json
import multiprocessing
import os
import struct
import sys
import time

PKG = "/sys/class/powercap/intel-rapl:0"
PSYS = "/sys/class/powercap/intel-rapl:1"
MSR = "/dev/cpu/0/msr"
LOW_W = 20.0


def rd(p):
    with open(p) as f:
        return f.read().strip()


def wr(p, v):
    with open(p, "w") as f:
        f.write(str(v))


def msr_rd(reg):
    fd = os.open(MSR, os.O_RDONLY)
    try:
        return struct.unpack("<Q", os.pread(fd, 8, reg))[0]
    finally:
        os.close(fd)


def msr_wr(reg, val):
    fd = os.open(MSR, os.O_WRONLY)
    try:
        os.pwrite(fd, struct.pack("<Q", val), reg)
    finally:
        os.close(fd)


def constraint(zone, name):
    for i in range(4):
        p = f"{zone}/constraint_{i}_name"
        if os.path.exists(p) and rd(p) == name:
            return f"{zone}/constraint_{i}_power_limit_uw"
    return None


def _spin(stop_at):
    x = 0
    while time.monotonic() < stop_at:
        x += 1


def load_and_sample(seconds=6.0):
    """All-core busy loop; package and psys power in W, 100 ms samples, after a 1 s ramp."""
    stop_at = time.monotonic() + seconds
    procs = [multiprocessing.Process(target=_spin, args=(stop_at,)) for _ in range(os.cpu_count() or 4)]
    for p in procs:
        p.start()
    time.sleep(1.0)
    samples = []
    last = (time.monotonic(), int(rd(f"{PKG}/energy_uj")), int(rd(f"{PSYS}/energy_uj")) if os.path.exists(PSYS) else None)
    while time.monotonic() < stop_at - 0.2:
        time.sleep(0.1)
        now = (time.monotonic(), int(rd(f"{PKG}/energy_uj")), int(rd(f"{PSYS}/energy_uj")) if os.path.exists(PSYS) else None)
        dt = now[0] - last[0]
        pkg = (now[1] - last[1]) / dt / 1e6
        psys = (now[2] - last[2]) / dt / 1e6 if now[2] is not None else None
        if pkg >= 0 and (psys is None or psys >= 0):          # skip counter wrap
            samples.append((pkg, psys))
        last = now
    for p in procs:
        p.join()
    pk = [s[0] for s in samples]
    ps = [s[1] for s in samples if s[1] is not None]
    return {"pkg_max_w": round(max(pk), 1), "pkg_avg_w": round(sum(pk) / len(pk), 1),
            "psys_max_w": round(max(ps), 1) if ps else None, "psys_avg_w": round(sum(ps) / len(ps), 1) if ps else None,
            "samples": len(samples)}


def pl3_fields(raw, unit_w):
    return {"raw": f"{raw:#018x}", "limit_w": round((raw & 0x7FFF) * unit_w, 3), "enabled": bool(raw & (1 << 15))}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--pl4-blob-w", type=float, default=107.0)
    ap.add_argument("--pl3-blob-w", type=float, default=100.0)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("needs root")
    os.makedirs(a.out, exist_ok=True)
    res = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}
    pl4 = constraint(PKG, "peak_power")
    fw_pl4 = rd(pl4) if pl4 else None
    unit_w = 1.0 / (1 << (msr_rd(0x606) & 0xF))
    fw_615 = msr_rd(0x615)
    res["firmware"] = {"pl1_w": int(rd(constraint(PKG, "long_term"))) / 1e6, "pl2_w": int(rd(constraint(PKG, "short_term"))) / 1e6,
                       "pl4_powercap": fw_pl4, "msr_601": f"{msr_rd(0x601):#018x}", "power_unit_w": unit_w,
                       "pl3": pl3_fields(fw_615, unit_w)}
    print("firmware:", json.dumps(res["firmware"]))
    try:
        res["steps"]["baseline"] = load_and_sample()
        print("baseline:", res["steps"]["baseline"])
        if pl4:
            wr(pl4, int(LOW_W * 1e6))
            res["steps"]["pl4_low"] = {"readback": rd(pl4), "msr_601": f"{msr_rd(0x601):#018x}", **load_and_sample()}
            print("PL4 20 W:", res["steps"]["pl4_low"])
            wr(pl4, int(a.pl4_blob_w * 1e6))
            res["steps"]["pl4_blob"] = {"readback": rd(pl4), "msr_601": f"{msr_rd(0x601):#018x}", **load_and_sample()}
            print(f"PL4 {a.pl4_blob_w:g} W:", res["steps"]["pl4_blob"])
            try:
                wr(pl4, fw_pl4)
            except OSError as e:
                res["steps"]["pl4_restore_error"] = str(e)
            res["steps"]["pl4_restored"] = {"readback": rd(pl4), "msr_601": f"{msr_rd(0x601):#018x}"}
            print("PL4 restored:", res["steps"]["pl4_restored"])
        units = int(round(LOW_W / unit_w))
        msr_wr(0x615, (fw_615 & ~0x7FFF) | units)
        res["steps"]["pl3_low"] = {"register": pl3_fields(msr_rd(0x615), unit_w), **load_and_sample()}
        print("PL3 20 W, enable as found:", res["steps"]["pl3_low"])
        if not fw_615 & (1 << 15):
            msr_wr(0x615, (fw_615 & ~0x7FFF) | units | (1 << 15))
            res["steps"]["pl3_low_enabled"] = {"register": pl3_fields(msr_rd(0x615), unit_w), **load_and_sample()}
            print("PL3 20 W, enabled:", res["steps"]["pl3_low_enabled"])
        msr_wr(0x615, (fw_615 & ~0x7FFF) | int(round(a.pl3_blob_w / unit_w)) & 0x7FFF)
        res["steps"]["pl3_blob"] = {"register": pl3_fields(msr_rd(0x615), unit_w), **load_and_sample()}
        print(f"PL3 {a.pl3_blob_w:g} W, enable as found:", res["steps"]["pl3_blob"])
    finally:
        errors = []
        if pl4 and fw_pl4 is not None:
            try:
                wr(pl4, fw_pl4)
            except OSError as e:
                errors.append(f"PL4 restore: {e}")
        try:
            msr_wr(0x615, fw_615)
        except OSError as e:
            errors.append(f"PL3 restore: {e}")
        res["restore_errors"] = errors
        if errors:
            print("RESTORE FAILED:", errors)
        res["restored"] = {"pl4_powercap": rd(pl4) if pl4 else None, "pl3": pl3_fields(msr_rd(0x615), unit_w)}
        print("restored:", res["restored"])
        with open(os.path.join(a.out, "pl34.json"), "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
