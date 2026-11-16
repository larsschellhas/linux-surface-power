"""A fake sysfs/procfs tree for adapter, supervisor and daemon tests. Mirrors the layout on the SB3."""
import os
import tempfile


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

    def cleanup(self):
        self.tmp.cleanup()
