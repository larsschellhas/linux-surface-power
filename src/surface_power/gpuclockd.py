"""surface-gpu-clockd: sets or removes the graphics-clock cap of the NVIDIA GPU, and nothing else.

Spec dgpu-standin-service 5. It is the only part with CAP_SYS_ADMIN, which the driver asks for the locked clocks [M], so it
stays small: no blob, no EC, no sysfs writes, no configuration. One request per connection on a Unix socket that only
root can open (and whose peer is checked): `ping`, `cap <mhz>`, `reset`; one answer line, `ok`, `ok 1` or
`error <kind> <text>`. It can only lower the clock or give it back.
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import struct
import sys
import time

from .adapters import gpu

SOCKET = "/run/surface-gpu-clock/sock"
PROTOCOL = 1
MIN_MHZ = 300
MAX_REQUEST = 32
CLIENT_TIMEOUT_S = 2.0
REQUEST = re.compile(rb"(ping|reset|cap [0-9]{1,5})\n")
NVML_GPU_IS_LOST = 15


class Refused(Exception):
    def __init__(self, kind: str, text: str):
        super().__init__(text)
        self.kind = kind


class Backend:
    """The two things the helper can do. NVML is opened for the one call and closed."""

    def __init__(self, root: str = "/", factory=gpu.Nvml):
        self.root = root
        self.factory = factory

    def _session(self):
        pci = gpu.find_nvidia_gpu(self.root)
        if pci is None:
            raise Refused("no-gpu", "no NVIDIA display controller (base detached?)")
        return self.factory(pci)

    def set_cap(self, mhz: int) -> None:
        with self._session() as nv:
            top = nv.max_clock_mhz()
            if not MIN_MHZ <= mhz <= top:
                raise Refused("range", f"{mhz} MHz is outside {MIN_MHZ}..{top}")
            nv.set_cap(mhz)

    def reset(self) -> None:
        with self._session() as nv:
            nv.reset_cap()


def handle(request: bytes, backend: Backend) -> str:
    """The answer to one request (without the newline)."""
    if not REQUEST.fullmatch(request):
        return "error request not one of: ping, cap <mhz>, reset"
    word = request[:-1].decode("ascii").split(" ")
    try:
        if word[0] == "ping":
            return f"ok {PROTOCOL}"                       # does not open NVML: a ping never wakes the GPU
        if word[0] == "cap":
            backend.set_cap(int(word[1]))
        else:
            backend.reset()
    except Refused as e:
        return f"error {e.kind} {e}"
    except gpu.NvmlError as e:
        kind = ("permission" if e.code == gpu.NVML_NO_PERMISSION
                else "no-gpu" if e.code == NVML_GPU_IS_LOST else "nvml")
        return f"error {kind} {e}"
    except Exception as e:                                # the server must survive whatever the library does
        return f"error nvml {type(e).__name__}: {e}"
    return "ok"


def default_allowed_uids(euid: int) -> set[int]:
    """Root only; run by hand without root (tests), also the own user, who then has no rights to set anything."""
    return {0, euid}


def _receive(conn: socket.socket, timeout: float) -> bytes:
    """Up to the first newline, the end of the stream, MAX_REQUEST + 1 bytes or the deadline, whichever comes first."""
    deadline, data = time.monotonic() + timeout, b""
    while b"\n" not in data and len(data) <= MAX_REQUEST:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        conn.settimeout(left)
        chunk = conn.recv(MAX_REQUEST + 1 - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _peer_uid(conn: socket.socket) -> int:
    return struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[1]


def _log(priority: str, text: str) -> None:
    level = {"warning": 4, "notice": 5}.get(priority, 6)
    print(f"<{level}>{text}", flush=True)


def _serve_one(conn: socket.socket, backend: Backend, allowed: set[int], timeout: float, log) -> None:
    request = _receive(conn, timeout)
    uid = _peer_uid(conn)
    answer = handle(request, backend) if uid in allowed else "error request peer not allowed"
    if answer.startswith("error"):
        log("warning", f"request {request[:MAX_REQUEST]!r} from uid {uid}: {answer}")
    conn.settimeout(timeout)
    conn.sendall((" ".join(answer.split()) + "\n").encode("utf-8", "replace"))
    # Take what is left of an overlong request: closing with unread data would reset the connection and lose the answer.
    conn.shutdown(socket.SHUT_WR)
    conn.settimeout(0.1)
    for _ in range(64):
        if not conn.recv(4096):
            break


def serve(sock: socket.socket, backend: Backend, allowed_uids: set[int] | None = None,
          client_timeout: float = CLIENT_TIMEOUT_S, stop=lambda: False, log=_log) -> None:
    """One connection after the other."""
    allowed = allowed_uids if allowed_uids is not None else default_allowed_uids(os.geteuid())
    while not stop():
        try:
            conn, _ = sock.accept()
        except TimeoutError:
            continue
        except OSError:
            if stop():
                return
            raise
        with conn:
            try:
                _serve_one(conn, backend, allowed, client_timeout, log)
            except OSError:
                pass                                      # a client that went away or stayed silent


def listen(path: str) -> socket.socket:
    """A socket of our own, for running by hand and for tests; the unit gets it from systemd."""
    if os.path.exists(path):
        os.remove(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    os.chmod(path, 0o600)
    sock.listen(4)
    return sock


def systemd_socket() -> socket.socket | None:
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or int(os.environ.get("LISTEN_FDS", "0")) < 1:
        return None
    return socket.socket(fileno=3)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="surface-gpu-clockd", description="Sets or removes the NVIDIA graphics-clock cap.")
    ap.add_argument("--socket", help=f"listen here instead of the socket systemd passes (the unit's is {SOCKET})")
    args = ap.parse_args(argv)
    sock = listen(args.socket) if args.socket else systemd_socket()
    if sock is None:
        print("no socket: start through surface-gpu-clockd.socket, or give --socket", file=sys.stderr)
        return 1
    try:
        serve(sock, Backend())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
