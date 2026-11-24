"""A fake sysfs/procfs tree for adapter, supervisor and daemon tests. Mirrors the layout on the SB3."""
import os
import struct
import tempfile

MMIO_ZONE = "sys/class/powercap/intel-rapl-mmio:0"
MMIO_PARAM = "sys/module/surface_rapl_mmio/parameters/allow_write"
PROC_THERMAL = "sys/bus/pci/devices/0000:00:04.0"
HOST_BRIDGE = "sys/bus/pci/devices/0000:00:00.0/config"
MCHBAR = 0xFED10000                     # the SB3's (Ice Lake) MCHBAR [M]
RAPL_UNITS = 0x000A0E03                 # MSR 0x606 and MCHBAR + 0x5938 on the SB3: power 1/8 W, energy, time unit 10 [M]


class FakeSys:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.write("sys/class/dmi/id/sys_vendor", "Microsoft Corporation")
        self.write("sys/class/dmi/id/product_name", "Surface Book 3")
        self.write("sys/class/dmi/id/product_sku", "Surface_Book_3_1899")
        os.makedirs(self.path("sys/bus/acpi/devices/MSHW0187:00"), exist_ok=True)
        self.write("sys/firmware/acpi/platform_profile", "performance")
        self.write("sys/kernel/security/lockdown", "[none] integrity confidentiality")
        # hwmon: surface_thermal with temp1..9, temp9 = I_VTS1; coretemp package.
        labels = [f"I_RTS{i}" for i in range(1, 9)] + ["I_VTS1"]
        for i, label in enumerate(labels, start=1):
            self.write(f"sys/class/hwmon/hwmon2/temp{i}_label", label)
            self.write(f"sys/class/hwmon/hwmon2/temp{i}_input", 30000)
        self.write("sys/class/hwmon/hwmon2/name", "surface_thermal")
        self.link("sys/class/hwmon/hwmon2/device", "sys/devices/platform/MSHW0117:00/serial0-0/01:03:01:00:02")
        self.write("sys/class/hwmon/hwmon8/name", "coretemp")
        self.write("sys/class/hwmon/hwmon8/temp1_label", "Package id 0")
        self.write("sys/class/hwmon/hwmon8/temp1_input", 50000)
        # power supplies
        self.write("sys/class/power_supply/ADP1/type", "Mains")
        self.write("sys/class/power_supply/ADP1/online", 1)
        for bat, cap in (("BAT1", 80), ("BAT2", 90)):
            self.write(f"sys/class/power_supply/{bat}/type", "Battery")
            self.write(f"sys/class/power_supply/{bat}/status", "Charging")
            self.write(f"sys/class/power_supply/{bat}/capacity", cap)
            self.write(f"sys/class/power_supply/{bat}/power_now", 0)
        self.write("proc/acpi/button/lid/LID0/state", "state:      open")
        # powercap
        pkg = "sys/class/powercap/intel-rapl:0"
        self.write(f"{pkg}/name", "package-0")
        self.write(f"{pkg}/enabled", 1)
        for i, (name, uw) in enumerate((("long_term", 30000000), ("short_term", 61000000), ("peak_power", 100000000))):
            self.write(f"{pkg}/constraint_{i}_name", name)
            self.write(f"{pkg}/constraint_{i}_power_limit_uw", uw)
        self.write(f"{pkg}/constraint_0_time_window_us", 27983872)
        self.write(f"{pkg}/constraint_0_max_power_uw", 15000000)  # the SB3 reports its TDP here
        psys = "sys/class/powercap/intel-rapl:1"
        self.write(f"{psys}/name", "psys")
        self.write(f"{psys}/enabled", 1)
        for i, (name, uw) in enumerate((("long_term", 25000000), ("short_term", 40000000))):
            self.write(f"{psys}/constraint_{i}_name", name)
            self.write(f"{psys}/constraint_{i}_power_limit_uw", uw)
        self.write(f"{psys}/constraint_0_time_window_us", 27983872)

    def path(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def write(self, rel: str, value) -> None:
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(f"{value}\n")

    def read(self, rel: str) -> str:
        with open(self.path(rel)) as f:
            return f.read().strip()

    def link(self, rel: str, target_rel: str) -> None:
        """Symlink `rel` -> `target_rel` (both relative to the root); creates the target directory."""
        os.makedirs(self.path(target_rel), exist_ok=True)
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        os.symlink(self.path(target_rel), self.path(rel))

    def link_raw(self, rel: str, target: str) -> None:
        """Symlink `rel` -> the literal `target` (e.g. "/dev/nvidia0" in a fake /proc/<pid>/fd)."""
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        os.symlink(target, self.path(rel))

    def add_base_sensors(self, celsius: float = 30.0) -> None:
        """Second surface_thermal for the SB3 base (KIP, below the base hub), as with the EC module v0.3."""
        labels = [f"I_RTS{i}" for i in range(1, 9)] + ["I_VTS1"]
        for i, label in enumerate(labels, start=1):
            self.write(f"sys/class/hwmon/hwmon10/temp{i}_label", label)
            self.write(f"sys/class/hwmon/hwmon10/temp{i}_input", int(celsius * 1000))
        self.write("sys/class/hwmon/hwmon10/name", "surface_thermal")
        self.link("sys/class/hwmon/hwmon10/device", "sys/devices/platform/MSHW0117:00/00:00:01:11:00/01:03:02:00:02")

    def remove_base_sensors(self, index: int = 10) -> None:
        """The base is taken off: its hwmon device disappears (spec supervisor 4.1)."""
        import shutil
        shutil.rmtree(self.path(f"sys/class/hwmon/hwmon{index}"), ignore_errors=True)

    def readd_base_sensors(self, index: int = 10, celsius: float = 31.0, name: str = "surface_thermal",
                           device: str = "sys/devices/platform/MSHW0117:00/00:00:01:11:00/01:03:02:00:02") -> None:
        """The base is attached again: the hub creates the device anew, possibly under another hwmon index (7.3.6-1-surface:
        hwmon5 -> hwmon11, the old index then belongs to a fan [M, 24.11.2026]). `name`/`device` give a foreign device."""
        labels = [f"I_RTS{i}" for i in range(1, 9)] + ["I_VTS1"]
        for i, label in enumerate(labels, start=1):
            self.write(f"sys/class/hwmon/hwmon{index}/temp{i}_label", label)
            self.write(f"sys/class/hwmon/hwmon{index}/temp{i}_input", int(celsius * 1000))
        self.write(f"sys/class/hwmon/hwmon{index}/name", name)
        link = self.path(f"sys/class/hwmon/hwmon{index}/device")
        if os.path.lexists(link):
            os.remove(link)
        self.link(f"sys/class/hwmon/hwmon{index}/device", device)

    def add_cpufreq(self, policies=((0, "0-3"), (4, "4-7")), lo=400000, hi=4000000):
        """cpufreq policies as on a CPU with clusters; scaling_max_freq starts at the maximum."""
        for n, cpus in policies:
            d = f"sys/devices/system/cpu/cpufreq/policy{n}"
            self.write(f"{d}/cpuinfo_min_freq", lo)
            self.write(f"{d}/cpuinfo_max_freq", hi)
            self.write(f"{d}/scaling_min_freq", lo)
            self.write(f"{d}/scaling_max_freq", hi)
            lst = []
            for part in cpus.split(","):
                a_, _, b_ = part.partition("-")
                lst += list(range(int(a_), int(b_ or a_) + 1))
            self.write(f"{d}/related_cpus", " ".join(map(str, lst)))

    def add_i915(self, card="card0", rp0=1100, rpn=300, cur=1100):
        d = f"sys/class/drm/{card}"
        self.write(f"{d}/gt_RP0_freq_mhz", rp0)
        self.write(f"{d}/gt_RPn_freq_mhz", rpn)
        self.write(f"{d}/gt_max_freq_mhz", cur)
        self.write(f"{d}/gt_min_freq_mhz", rpn)

    def add_devfreq_gpu(self, name="3d00000.gpu", freqs=(200000000, 400000000, 600000000, 800000000)):
        d = f"sys/class/devfreq/{name}"
        self.write(f"{d}/available_frequencies", " ".join(map(str, freqs)))
        self.write(f"{d}/min_freq", freqs[0])
        self.write(f"{d}/max_freq", freqs[-1])

    # --- the MMIO copy of PL1/PL2 (specs kernel-mmio-rapl.md, kernel-mmio-daemon.md 12) ---

    def add_mmio_zone(self, kind: str = "module", allow_write: str = "Y", enabled: int = 0, pl1_uw: int = 25_000_000,
                      pl2_uw: int = 61_000_000, window_us: int = 27983872) -> str:
        """An `intel-rapl-mmio:0` zone as the RAPL core shows it. `kind` "module": made by surface_rapl_mmio, with its parameter
        `allow_write` ("Y"/"N"); "kernel": made by the processor thermal driver (PCI 00:04.0 present), with a PL4 constraint and a
        DRAM sub-zone. Values are the SB3's firmware MMIO copy (25/61 W, disabled) [M]. Returns the zone path relative to the root."""
        z = MMIO_ZONE
        self.write(f"{z}/name", "package-0")
        self.write(f"{z}/enabled", enabled)
        self.write(f"{z}/energy_uj", 0)
        for i, (name, uw, win, mx) in enumerate((("long_term", pl1_uw, window_us, 15000000),
                                                 ("short_term", pl2_uw, 2440, 0))):
            self.write(f"{z}/constraint_{i}_name", name)
            self.write(f"{z}/constraint_{i}_power_limit_uw", uw)
            self.write(f"{z}/constraint_{i}_time_window_us", win)
            self.write(f"{z}/constraint_{i}_max_power_uw", mx)
        if kind == "module":
            self.write(MMIO_PARAM, allow_write)
        elif kind == "kernel":
            os.makedirs(self.path(PROC_THERMAL), exist_ok=True)
            self.write(f"{z}/constraint_2_name", "peak_power")
            self.write(f"{z}/constraint_2_power_limit_uw", 0)
            dram = f"{z}:0"
            self.write(f"{dram}/name", "dram")
            self.write(f"{dram}/enabled", 0)
            self.write(f"{dram}/constraint_0_name", "long_term")
            self.write(f"{dram}/constraint_0_power_limit_uw", 0)
        else:
            raise ValueError(kind)
        return z

    def add_msr(self, values: dict[int, int] | None = None) -> None:
        """/dev/cpu/0/msr as a regular file (msr.read/write use positioned I/O). 0x606 defaults to the SB3's units."""
        regs = {0x606: RAPL_UNITS, **(values or {})}
        dev = self.path("dev/cpu/0/msr")
        os.makedirs(os.path.dirname(dev), exist_ok=True)
        if not os.path.exists(dev):
            with open(dev, "wb") as f:
                f.write(bytes(0x700))
        with open(dev, "r+b") as f:
            for reg, v in regs.items():
                f.seek(reg)
                f.write(struct.pack("<Q", v))

    def add_host_bridge(self, mchbar: int = MCHBAR | 1, size: int | None = None) -> None:
        """The host bridge's PCI config with MCHBAR at 0x48; `size` cuts it (64 = the view without CAP_SYS_ADMIN)."""
        data = bytes(0x48) + struct.pack("<Q", mchbar) + bytes(0x100 - 0x50)
        p = self.path(HOST_BRIDGE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data if size is None else data[:size])

    def add_mmio_register(self, value: int, units: int = RAPL_UNITS, mchbar: int = MCHBAR) -> None:
        """/dev/mem as a sparse file that holds the MMIO copy at MCHBAR + 0x59a0 and the unit register at MCHBAR + 0x5938, so
        that rapl_copies reads it through its real mmap path. Adds the host bridge too."""
        self.add_host_bridge(mchbar | 1)
        p = self.path("dev/mem")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "ab"):
            pass
        os.truncate(p, max(os.path.getsize(p), mchbar + 0x6000))
        with open(p, "r+b") as f:
            f.seek(mchbar + 0x59A0)
            f.write(struct.pack("<Q", value))
            f.seek(mchbar + 0x5938)
            f.write(struct.pack("<Q", units))

    def mmio_register(self, mchbar: int = MCHBAR) -> int:
        with open(self.path("dev/mem"), "rb") as f:
            f.seek(mchbar + 0x59A0)
            return struct.unpack("<Q", f.read(8))[0]

    def cleanup(self):
        self.tmp.cleanup()
