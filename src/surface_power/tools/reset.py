"""surface-power-reset: ExecStopPost of surface-powerd (FR-S2). Restores the firmware snapshot if the daemon died without doing
it (SIGKILL, OOM, watchdog), then deletes it. If the marker of a GPU clock cap is left, it asks the clock helper to remove the cap
(spec dgpu-standin-service 6). Without a snapshot and a marker it does nothing. It does not import the engine."""
import argparse
import os
import sys

from ..adapters import gpuclock
from ..adapters.gpu import NvmlError
from ..supervisor import Snapshot

RUN_DIR = "/run/surface-power"


def reset_gpu_cap(run_dir: str, socket_path: str) -> int:
    marker = os.path.join(run_dir, "gpu-cap")
    if not os.path.exists(marker):
        return 0
    try:
        gpuclock.ClockHelper(socket_path, timeout=5.0).reset_cap()
    except NvmlError as e:
        print(f"GPU clock cap not removed: {e}", file=sys.stderr)
        return 1
    os.remove(marker)
    print("GPU clock cap removed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="surface-power-reset")
    ap.add_argument("--run-dir", default=RUN_DIR)
    ap.add_argument("--gpu-clock-socket", default=gpuclock.SOCKET)
    args = ap.parse_args(argv)
    gpu_code = reset_gpu_cap(args.run_dir, args.gpu_clock_socket)
    path = os.path.join(args.run_dir, Snapshot.FILE)
    if not os.path.exists(path):
        print("no snapshot, nothing to do (the daemon restored the firmware values itself)")
        return gpu_code
    snap = Snapshot.load(path)
    if snap is None:
        print(f"snapshot {path} is unreadable; left in place", file=sys.stderr)
        return 1
    errors = snap.restore()
    if errors:
        print("restore failed: " + "; ".join(errors), file=sys.stderr)
        return 1
    snap.remove()
    print(f"firmware values restored ({len(snap.values)} files)")
    return gpu_code


if __name__ == "__main__":
    sys.exit(main())
