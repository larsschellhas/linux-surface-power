#!/usr/bin/env python3
"""Summary of a field-test run (scripts/field-test.py) in the terms of the acceptance criteria AC-L1 - AC-L3 and the Windows reference.

Usage: python3 scripts/field-test-report.py OUT/field-test.csv [--tail-min 5]
Prints: cut-in (time, skin, PL1 before/after), PL1 steps after the cut, the last minutes of the load (skin range, PL1, package and
system power), states seen, and when PL1 returned to its table value after the load.
"""
import argparse
import csv
import statistics as st


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--tail-min", type=float, default=5, help="length of the steady-state window at the end of the load")
    a = ap.parse_args()

    with open(a.csv) as f:
        rows = [r for r in csv.DictReader(f)]
    for r in rows:
        for k in ("t_s", "skin_c", "cpu_c", "pkg_w", "sys_w", "pl1_w", "pl2_w", "psys_pl1_w", "psys_pl2_w"):
            r[k] = num(r.get(k))
    load = [r for r in rows if r["phase"] == "load"]
    idle = [r for r in rows if r["phase"] == "idle"]
    if not load:
        print("no load phase in this run")
        return 1
    t_load = load[0]["t_s"]
    table_pl1 = load[0]["pl1_w"]

    print(f"load: {len(load) / 60:.1f} min, start skin {load[0]['skin_c']} °C, table PL1/PL2 {table_pl1}/{load[0]['pl2_w']} W, "
          f"psys {load[0]['psys_pl1_w']}/{load[0]['psys_pl2_w']} W")
    print(f"states: {sorted({r['state'] for r in rows if r['state']})}")

    # Cut-in: first PL1 change below the table value.
    cut = next((r for r in load if r["pl1_w"] is not None and r["pl1_w"] < table_pl1 - 0.05), None)
    if cut:
        print(f"cut: after {cut['t_s'] - t_load:.0f} s of load, skin {cut['skin_c']} °C, PL1 {table_pl1} -> {cut['pl1_w']} W")
        changes = [(b["t_s"], round(b["pl1_w"] - p["pl1_w"], 3)) for p, b in zip(load, load[1:])
                   if b["t_s"] > cut["t_s"] and b["pl1_w"] != p["pl1_w"]]
        if changes:
            sizes = [abs(d) for _, d in changes]
            gaps = [t2 - t1 for (t1, _), (t2, _) in zip(changes, changes[1:])]
            print(f"steps after the cut: {len(changes)}, size {min(sizes):.2f}–{max(sizes):.2f} W (median {st.median(sizes):.2f} W), "
                  f"interval median {st.median(gaps) if gaps else '-'} s, longest hold {max(gaps) if gaps else '-'} s")
    else:
        print("cut: none (the skin controller did not engage)")

    tail = [r for r in load if r["t_s"] >= load[-1]["t_s"] - a.tail_min * 60]
    mean = lambda k: st.mean(r[k] for r in tail if r[k] is not None)
    print(f"last {a.tail_min:g} min of load: skin {min(r['skin_c'] for r in tail)}–{max(r['skin_c'] for r in tail)} °C "
          f"(mean {mean('skin_c'):.2f}), PL1 {min(r['pl1_w'] for r in tail)}–{max(r['pl1_w'] for r in tail)} W, "
          f"package {mean('pkg_w'):.1f} W, system {mean('sys_w'):.1f} W, CPU max {max(r['cpu_c'] for r in tail)} °C")
    print(f"max over the run: skin {max(r['skin_c'] for r in rows if r['skin_c'])} °C, system {max(r['sys_w'] for r in rows if r['sys_w'])} W")

    if idle:
        back = next((r for r in idle if r["pl1_w"] is not None and r["pl1_w"] >= table_pl1 - 0.05), None)
        print(f"after the load: PL1 back to {table_pl1} W after {back['t_s'] - idle[0]['t_s']:.0f} s (skin {back['skin_c']} °C)"
              if back else f"after the load: PL1 not back to {table_pl1} W within {len(idle)} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
