"""Client of the clock helper surface-gpu-clockd (spec dgpu-standin-service 5.2, 6): one request per connection."""
from __future__ import annotations

import socket

from .gpu import NvmlError

SOCKET = "/run/surface-gpu-clock/sock"
TIMEOUT_S = 1.0           # inside the watchdog time of the daemon's loop
PING_TIMEOUT_S = 5.0      # the first request starts the helper through its socket
PERMANENT = ("permission", "request", "range")      # a defect or a setup fault: retrying cannot help


class HelperError(NvmlError):
    """`kind`: as the helper answers (permission, request, range, no-gpu, nvml), or `unreachable` for the socket."""

    def __init__(self, kind: str, text: str):
        super().__init__(f"clock helper: {kind}: {text}", permanent=kind in PERMANENT)
        self.kind = kind
        self.short = f"{kind}: {text}"


class ClockHelper:
    def __init__(self, path: str = SOCKET, timeout: float = TIMEOUT_S):
        self.path = path
        self.timeout = timeout

    def _request(self, line: str, timeout: float | None = None) -> str:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout or self.timeout)
                s.connect(self.path)
                s.sendall(line.encode("ascii") + b"\n")
                data = b""
                while not data.endswith(b"\n") and len(data) < 512:
                    chunk = s.recv(512)
                    if not chunk:
                        break
                    data += chunk
        except OSError as e:
            raise HelperError("unreachable", f"{self.path}: {e.strerror or e}") from None
        answer = data.decode("utf-8", "replace").strip()
        if answer == "ok" or answer.startswith("ok "):
            return answer
        word = answer.split(" ", 2)
        if len(word) >= 2 and word[0] == "error":
            raise HelperError(word[1], word[2] if len(word) > 2 else "")
        raise HelperError("unreachable", f"unexpected answer {answer[:80]!r}")

    def ping(self) -> None:
        answer = self._request("ping", PING_TIMEOUT_S)
        if answer != "ok 1":
            raise HelperError("request", f"protocol {answer!r}, this daemon speaks 1")

    def set_cap(self, mhz: int) -> None:
        self._request(f"cap {int(mhz)}")

    def reset_cap(self) -> None:
        self._request("reset")
