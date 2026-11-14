"""surface-power-notify: desktop notifications from status.json, run as a user unit (FR-M5, spec monitoring 4).

Notifies on: entering emergency or yielded, return to control, and the battery discharging on AC for more than 60 s (the charger
is too weak). The same cause is notified at most once per 10 minutes.
"""
import json
import shutil
import subprocess
import sys
import time

STATUS = "/run/surface-power/status.json"
REPEAT_MS = 10 * 60 * 1000
DISCHARGE_MS = 60 * 1000
DISCHARGE_MW = 1000   # more than 1 W discharge counts


class Notifier:
    def __init__(self):
        self.state = None
        self.last: dict[str, int] = {}
        self.discharge_since: int | None = None

    def _once(self, key: str, now_ms: int) -> bool:
        if now_ms - self.last.get(key, -REPEAT_MS - 1) < REPEAT_MS:
            return False
        self.last[key] = now_ms
        return True

    def update(self, status: dict, now_ms: int) -> list[tuple[str, str]]:
        """Return (title, body) pairs to show."""
        out = []
        state = status.get("state")
        if state != self.state:
            if state in ("emergency", "yielded") and self._once(state, now_ms):
                body = next((e["text"] for e in reversed(status.get("events", [])) if state in e.get("text", "")), state)
                title = "Surface power: emergency, firmware limits" if state == "emergency" else "Surface power: handed off"
                out.append((title, body))
            elif state == "control" and self.state in ("emergency", "yielded"):
                out.append(("Surface power: back to normal", "Limits follow the device configuration again."))
            self.state = state
        ac = (status.get("slider") or {}).get("ac")
        discharge = next((i.get("value") for i in status.get("inputs", []) if i.get("channel") == "28B7BD85/10"), None)
        if ac and discharge is not None and discharge > DISCHARGE_MW:
            if self.discharge_since is None:
                self.discharge_since = now_ms
            elif now_ms - self.discharge_since > DISCHARGE_MS and self._once("charger", now_ms):
                out.append(("Surface power: charger too weak", "The battery discharges although the charger is connected."))
        else:
            self.discharge_since = None
        return out


def main(argv=None) -> int:
    path = (argv or sys.argv[1:] or [STATUS])[0]
    n = Notifier()
    send = shutil.which("notify-send")
    while True:
        try:
            with open(path) as f:
                status = json.load(f)
        except (OSError, ValueError):
            status = None
        if status:
            for title, body in n.update(status, int(time.monotonic() * 1000)):
                if send:
                    subprocess.run([send, "--app-name=surface-power", title, body], check=False)
                else:
                    print(f"{title}: {body}", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
