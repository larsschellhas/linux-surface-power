"""Monitoring (spec monitoring.md): journal lines with priority prefixes, the status file, the time series."""
import csv
import datetime as dt
import glob
import json
import os
import sys
from collections import deque

PRIORITY = {"error": 3, "warning": 4, "notice": 5, "info": 6, "debug": 7}


class Journal:
    """Writes `<N>text` lines to stderr; systemd-journald turns the prefix into the priority. Keeps the last events."""

    def __init__(self, stream=None, keep: int = 20):
        self.stream = stream or sys.stderr
        self.events: deque = deque(maxlen=keep)

    def log(self, priority: str, text: str) -> None:
        print(f"<{PRIORITY[priority]}>{text}", file=self.stream, flush=True)
        if PRIORITY[priority] <= PRIORITY["info"]:
            self.events.append({"time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                "priority": priority, "text": text})


def write_status(run_dir: str, status: dict) -> None:
    """Atomic write of status.json (mode 0644), limited to 64 KiB."""
    os.makedirs(run_dir, exist_ok=True)
    data = json.dumps(status, indent=1, ensure_ascii=False)
    if len(data) > 65536:
        status = dict(status, events=[], inputs=[])
        data = json.dumps(status, indent=1, ensure_ascii=False)
    path = os.path.join(run_dir, "status.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


class Series:
    """One CSV row per engine run in <state_dir>/series-YYYYMMDD.csv; older than 7 days or beyond 50 MiB is deleted."""

    MAX_AGE_DAYS, MAX_BYTES = 7, 50 * 1024 * 1024

    def __init__(self, state_dir: str, columns: list[str]):
        self.dir, self.columns = state_dir, columns
        self.day = None
        self.file = None
        self.writer = None

    def _open(self, today: str) -> None:
        if self.file:
            self.file.close()
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"series-{today}.csv")
        new = not os.path.exists(path)
        self.file = open(path, "a", newline="")
        self.writer = csv.writer(self.file, lineterminator="\n")
        if new:
            self.writer.writerow(self.columns)
        self.day = today
        self._prune()

    def _prune(self) -> None:
        files = sorted(glob.glob(os.path.join(self.dir, "series-*.csv")))
        cutoff = (dt.date.today() - dt.timedelta(days=self.MAX_AGE_DAYS)).strftime("%Y%m%d")
        for f in files:
            if os.path.basename(f)[7:15] < cutoff:
                os.remove(f)
        files = sorted(glob.glob(os.path.join(self.dir, "series-*.csv")))
        while len(files) > 1 and sum(os.path.getsize(f) for f in files) > self.MAX_BYTES:
            os.remove(files.pop(0))

    def write(self, row: list) -> None:
        today = dt.date.today().strftime("%Y%m%d")
        if today != self.day:
            self._open(today)
        self.writer.writerow(row)
        self.file.flush()

    def close(self) -> None:
        if self.file:
            self.file.close()
            self.file = None
