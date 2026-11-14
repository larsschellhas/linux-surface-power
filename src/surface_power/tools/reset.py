"""surface-power-reset: ExecStopPost of surface-powerd (FR-S2). Restores the firmware snapshot if the daemon died without doing
it (SIGKILL, OOM, watchdog), then deletes it. Without a snapshot it does nothing. It does not import the engine."""
import argparse
import os
import sys

from ..supervisor import Snapshot

RUN_DIR = "/run/surface-power"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="surface-power-reset")
    ap.add_argument("--run-dir", default=RUN_DIR)
    args = ap.parse_args(argv)
    path = os.path.join(args.run_dir, Snapshot.FILE)
    if not os.path.exists(path):
        print("no snapshot, nothing to do (the daemon restored the firmware values itself)")
        return 0
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
