"""The tester kit's parts (spec docs/specs/tester-kit.md). `tools/check.py` runs them in order.

Stages: S0 preflight, S1 snapshot, S2 observe under load, S3 module read only, S4 module with write permission and no write,
S5 first write (only downwards). Every part works below a `root` directory, so that the tests run it on a fake sysfs.
"""
import os

KIT_VERSION = "0.3.0"
SCHEMA = 1                                     # of the JSON block in the result (tools/check_eval.py reads it)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
MODULE_SRC = os.path.join(REPO, "kernel", "surface-rapl-mmio")
ZONE = "sys/class/powercap/intel-rapl-mmio:0"
SECURE_BOOT = "sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"

# Fixed MCHBAR address per platform type, as the module and Windows assume it (`kernel-mmio-rapl.md` 3; research/15 2)
MCHBAR_BY_PLATFORM = {"icelake": 0xFED10000, "tigerlake": 0xFEDC0000, "alderlake": 0xFEDC0000, "raptorlake": 0xFEDC0000,
                      "meteorlake": 0xFEDC0000, "lunarlake": 0xFEDC0000}
# Platform type per profile for the module stages (`kernel-mmio-rapl.md` 6.1 item 2; types from `research/15` 2, device-to-type mapping from the
# earlier driver notes, not re-verified there [H]). A wrong type only makes the module refuse: its MCHBAR check compares the config with the constant.
PLATFORM = {
    "surface-book-3.toml": "icelake", "surface-book-3-15.toml": "icelake", "surface-laptop-go.toml": "icelake", "surface-go-3.toml": "icelake",
    "surface-pro-7-plus.toml": "tigerlake", "surface-pro-8.toml": "tigerlake", "surface-laptop-4-intel.toml": "tigerlake",
    "surface-laptop-go-2.toml": "tigerlake", "surface-laptop-studio.toml": "tigerlake",
    "surface-pro-9.toml": "alderlake", "surface-laptop-5.toml": "alderlake", "surface-laptop-go-3.toml": "alderlake",
    "surface-go-4.toml": "alderlake", "surface-laptop-studio-2.toml": "raptorlake",
    "surface-pro-10.toml": "meteorlake", "surface-pro-10-5g.toml": "meteorlake", "surface-laptop-6.toml": "meteorlake",
    "surface-laptop-7-5g.toml": "meteorlake", "surface-pro-11-intel.toml": "lunarlake", "surface-laptop-7-intel.toml": "lunarlake",
}


def read(root: str, rel: str) -> str | None:
    try:
        with open(os.path.join(root, rel), errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def read_int(root: str, rel: str) -> int | None:
    v = read(root, rel)
    try:
        return None if v is None else int(v)
    except ValueError:
        return None
