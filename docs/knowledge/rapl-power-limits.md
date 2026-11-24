# Package power limits: the two copies, the RAPL core, readers and writers

24.11.2026. Details and evidence in `../research/15-mmio-evidence.md`, `../specs/kernel-mmio-rapl.md`, `../specs/kernel-mmio-daemon.md`,
`../specs/deviations.md` DV-8. Kernel source: linux-surface `v7.3-surface-devel` (commit `521f20ef`).

## 1. Two copies, the lower enabled one is in force
- PL1/PL2 exist twice: the **MSR copy** `0x610` (Linux powercap `intel-rapl:0`) and the **MMIO copy** MCHBAR + `0x59a0` (powercap `intel-rapl-mmio:0`, which
  exists only with the processor thermal device 00:04.0 or the module `surface_rapl_mmio`). Same layout: PL1 bits 14:0, enable 15, clamp 16, window 23:17,
  lock 31; PL2 the same at +32 [C].
- **Windows writes the MMIO copy** (channels 51/52/61) with enable and clamp (`0x18000`) on every write, in every Intel client build of the corpus, and leaves the
  MSR copy alone (channels 56/57 used by no blob) [C, `research/15` 1]. **Linux writes the MSR copy** by default (DV-8); since 24.11.2026 the daemon writes the MMIO copy where a writable MMIO zone exists (`kernel-mmio-daemon.md`;
  checked on the SB3: 9.98 W at a 10 W MMIO PL1, MSR copy 30 W [M]).
- **The lower enabled copy is in force:** measured on the Pro 10 (MSR below MMIO, tester), the Pro 8 (MMIO below MSR, tester) and the SB3 (MMIO enabled through
  the module at 8 W against MSR 30 W: package 8.0 W, 24.11.2026) [M]; as a general rule still [H]. A disabled copy does not limit [M, SB3].
- Firmware state differs per device: SB3 MMIO 25/61 W **disabled** (PL1 clamp 1), MSR 30/61 W; Pro 8 MMIO 30/60 W enabled, MSR 35/60 W; Pro 9 MMIO 30/60 W
  enabled, MSR 35/60 W; Pro 10 MMIO 28/35 W enabled, MSR 35/64 W [M, `research/15` 4]. Never transfer these values.
- On the SB3 the blob's highest PL1/PL2 (30/61 W) equal the firmware MSR copy [M, `research/02`], so leaving the MSR copy at its firmware value costs nothing
  there; on the Pro 10 the blob allows more than the MSR copy (`kernel-mmio-daemon.md` 3).

## 2. Enable and clamp
- The powercap attribute `enabled` is **not one bit**: writing 1 sets PL1 enable, then PL1 clamp, PL2 enable and PL2 clamp; writing 0 clears all four [C,
  `set_domain_enable`, `rapl_default_set_floor_freq`]. Reading it returns the **PL1 enable bit only** (0 when locked) [C, `get_domain_enable`].
- `enabled=1` reports success even when the PL1 enable bit did not stick, and the three other writes are not checked [C]. Read the register to be sure.
- A write to a constraint file changes only the limit field [C, M SB3].
- SB3 (Ice Lake) [M, 24.11.2026]: **enable alone makes a limit effective, for PL1 and PL2 independently; clamp alone does nothing; the clamp was not needed with
  `scaling_min_freq` 2.0 GHz.** Other CPUs: not measured.
- `enabled=0` also clears a clamp bit the firmware had set (SB3: PL1 clamp) [M]; only the module's restore at unload or a cold boot brings it back.

## 3. What the RAPL core does with a write
- **Wrap above the field:** the value is converted to units, shifted and masked to the field width before the driver sees it; `set_power_limit` has no upper
  check. From 4096 W a value wraps silently (5 kW → 904 W) [C `rapl_write_data_raw`, M SB3]. Applies to every RAPL zone. User space must bound its values.
- **No usable maximum:** `constraint_*_max_power_uw` is not a bound (SB3 MSR zone: 15 W TDP; MMIO zone: 15 W and 0, info register min/max 0 [M]).
- **Time window:** encoded by the core, rounded down (value / 976 µs; exponent and two-bit fraction) [C]; the daemon's 28 steps (1 s … 112 s, four per octave)
  each land on the intended code, read-back = 0.9994 × written [C computed, M for six steps on the SB3]. Assumes the time unit 1/1024 s.
- **Error codes:** any failed write to a constraint file reaches user space as `ENODATA`, to `enabled` as `ENOSYS`, whatever the cause [C, `powercap_sys.c`].
- **Resume:** the core saves the limits before suspend and writes back **only the limit fields** after resume (non-zero ones), never enable or clamp [C]. SB3: the
  MMIO register survived three s2idle cycles unchanged [M].
- **Removal of a package** (module unload, driver removal) clears enable and clamp of every limit [C, `rapl_remove_package_cpuslocked`]; `surface_rapl_mmio`
  refuses those writes and restores the register itself.
- **Turbo budget:** PL1 limits an average over its window. After an idle phase the package may run above PL1 (up to PL2 and psys PL2) for some seconds: on the SB3
  ~10 s after a resume, consistent with a 28 s average [M for the burst, H for the explanation].
  Without suspend, after 60 s idle at a 10 W MMIO PL1: 3–4 s up to 40 W, the package temperature briefly at 100 °C (TjMax), then PL1 [M, SB3, 24.11.2026].

## 4. Reading the MMIO copy
- The address (MCHBAR) is in the PCI config of 00:00.0 at `0x48`. **Without `CAP_SYS_ADMIN` the kernel shows only the first 64 bytes of a PCI config** (128 for
  CardBus) and returns 0 bytes beyond, without an error [C, `pci-sysfs.c`, `proc.c`]. `/proc/iomem` shows 0 addresses without it [C].
- `/dev/mem` needs `CAP_SYS_RAWIO` and lockdown `none` [C, `mem.c`]. The daemon's units have `CAP_DAC_OVERRIDE CAP_SYS_RAWIO`, so the daemon cannot find the
  address under its units (plan B11) [M, SB3]; since 24.11.2026 it logs the reason once per run and shows it in `status.json`
  (`rapl_copies.mmio_unreadable`) [C]. `surface-power-report` as root can read the copy.
- The module zone gives PL1, PL2, window and PL1 enable without any address; not PL2 enable, clamp or lock [C]. The daemon reads the copy from a zone wherever one
  exists (since 24.11.2026, source `zone` in `status.json`).

## 5. Without the daemon the SB3 is held by psys
With the daemon stopped, the firmware's psys limits 25 W (long) / 40 W (short) hold the whole platform: under full load the package sits at ~13–15 W after a
~10 s burst at psys 40 W [M, 23.–24.11.2026]. A limit above ~13 W cannot show an effect in a test without the daemon.

## 6. thermald
Installed: 2.5.6 (Ubuntu unit with `--adaptive`); read: tag `v2.5.6` and master [C]. In 2.5.6, adaptive mode with an MMIO zone writes `enabled=0` to
`intel-rapl:0` (master no longer does); its RAPL cooling device writes `enabled` 1/0 on the zone it drives; `--workaround-enabled` writes `enabled=0` to the MMIO
zone every ~30 s (Kaby Lake: directly through `/dev/mem`); no RAPL restore at exit found. Adaptive mode needs an INT3400-type device with GDDV; the SB3 has none.
thermald must not run next to the daemon (`Conflicts=`) (`../specs/kernel-mmio-rapl.md` 7).
