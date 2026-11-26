"""The real clock-helper server in a thread, with a fake NVML behind it (spec dgpu-standin-service 5)."""
import threading

from surface_power import gpuclockd


class HelperThread:
    def __init__(self, path, root, nvml, allowed_uids=None, client_timeout=2.0):
        self.sock = gpuclockd.listen(path)
        self.sock.settimeout(0.05)
        self.stopping = threading.Event()
        backend = gpuclockd.Backend(root, lambda pci: nvml)
        self.thread = threading.Thread(
            target=gpuclockd.serve, daemon=True,
            args=(self.sock, backend),
            kwargs={"allowed_uids": allowed_uids, "client_timeout": client_timeout, "stop": self.stopping.is_set,
                    "log": lambda *_: None})
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.thread.join(5)
        self.sock.close()
