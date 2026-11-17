# Spec: supervisor

Version 0.1, 14.11.2026. Implements FR-D1 – FR-D3, FR-S1 – FR-S7, FR-M4, NFR-5. Tests: AC-S1 – AC-S8. Markers as in `blob-format.md`.
It takes over the proven parts of the prototype service (`../research/06-prototype-and-related-work.md`).

## 1. States
```
start ─► detect ─► (unsupported) ─► idle (no writes, exit 0)
             │
             ▼
         snapshot ─► dry-run ◄──────────────┐
             │          (untested device,  │
             ▼           or --dry-run)     │
         control ─► yielded (foreign change, read-only) │
             │  ▲                           │
             ▼  │ recover                   │
         emergency (firmware values) ───────┘
             │
             ▼
         stop: restore snapshot, remove it
```
- **control:** engine outputs are written through the output stage.
- **dry-run:** everything is computed and logged; nothing is written (FR-M4).
- **emergency:** firmware values are written; the engine keeps running, so that recovery is possible.
- **yielded:** firmware values are written once, then nothing more until restart. The engine keeps running for the status file.

## 2. Device detection (FR-D1 – FR-D3)
1. **DMI:** `sys_vendor == "Microsoft Corporation"`; the product name and SKU come from `/sys/class/dmi/id`.
2. **SMF presence:** any of
   - an ACPI device `MSHW0187` (`/sys/bus/acpi/devices/MSHW0187:*`);
   - the ACPI thermal zone `\TPOL`. On the SB3, `MSHW0187` is the `_HID` of this thermal zone; Linux enumerates it as `LNXTHERM:00` with path `\TPOL`, so the HID itself is not visible in sysfs [M];
   - a non-empty UEFI variable `SMF_DATA_BLOB-…`.

   A blob source is needed in any case: the UEFI variable or a configured file.
3. **Profile lookup:** the device profile is looked up by product name and SKU (6).
   - No profile, but SMF and a blob: this is an "untested SMF device". The default is dry-run (FR-D3); `--control` overrides it.
   - No SMF or no blob: idle, with a clear message (FR-D2).
4. **Blob check:** the blob is loaded and checked (`blob-format.md`). Any error leads to idle with the error (G-4).
5. **Missing inputs:** the daemon reports every input the blob reads but this system never provides. Each one is checked against the outputs it can reach, directly or through internal nodes:
   - **It reaches only log-only outputs:** allowed. On the SB3 these are the base sensors 605/609, which reach only the dGPU limit and the thermal-policy outputs. Their controllers stay OFF.
   - **It reaches a limit that Linux writes:** control is not allowed, only dry-run, and the reason is logged. Otherwise the blob would run on a fallback that is meant for a lost sensor, not for normal operation.
   - **Documented deviation with a Windows fallback:** never blocks control. This is the case for the display brightness (DV-3).
   - **Gets a Linux target later:** once an output gets one (e.g. a dGPU stand-in), its inputs become required automatically.
6. **Outputs without a Linux path (2.6) [D]:** control is allowed only when every output of the blob is written: a powercap or MSR
   target, the dGPU stand-in, a thermal action, a frequency cap for a throttle request, or a fan and charging output with a path on this
   machine: a SAM request through the debug device (root and `surface_aggregator_cdev`), or the kernel's own path for 201 and 301
   (`outputs.md` 9), or an output that needs none, with evidence (`outputs.md` 10). Otherwise the blob would regulate, for example, the CPU while its fan,
   throttle or safety outputs go nowhere. The daemon then runs in dry-run and logs the outputs. `--allow-partial-outputs` overrides
   this for field tests; the status file lists the outputs in `outputs_without_linux_path`.
7. **Conflicts:** if thermald is active (`systemctl is-active thermald`, or its D-Bus name), the daemon refuses to start (FR-S6). systemd `Conflicts=` normally prevents this. In every mode, the daemon logs a warning and writes `competing_writers` to the status file while thermald, tuned, TLP or power-profiles-daemon runs (checked at start and every 60 s): they write limits too, so the sysfs values of a dry-run differ from the computed ones. `surface-power-report` prints the same warning.

## 3. Snapshot and restore (FR-S1, FR-S2)
- **Contents:** before the first write, the supervisor saves every value it may write:
  - package PL1, PL2, PL4 with their time windows;
  - psys PL1, PL2;
  - the MSR 0x615 value, if PL3 is written (snapshot key `msr:<device>@0x615`, the full register as hex);
  - `scaling_max_freq` of the cpufreq policies and the GPU maximum-frequency file, if throttle requests are written (`outputs.md` 8);
  - for the fan and charging outputs (`outputs.md` 9), which cannot be read back: the neutral frame of every channel the daemon has
    sent (key `fan:<channel>`, section `fan` of the file). It is written before the first send of the channel. Channels without a
    known neutral value (the opt-in temperature thresholds) are listed as `unrestorable`.

  The file is `/run/surface-power/snapshot.json`, written atomically (write a temp file, then rename).
- **Predecessor:** if a snapshot from a crashed predecessor exists, it is kept and used as is. The current values may be the daemon's own.
- **Restore:**
  - on normal exit;
  - on any unhandled exception;
  - on `PrepareForSleep(true)`;
  - on entering emergency or yielded.

  Order: time window before PL1. Only values that differ are written. The fan frames are always sent (nothing can be compared),
  once, and then dropped from the snapshot; the daemon sends everything again on the first cycle after a resume or after the emergency ends.
- **After restore on exit:** the snapshot is deleted.
- **Reset tool:** `surface-power-reset` (`ExecStopPost`) restores from the file if it still exists, then deletes it. It does not import the engine.

## 4. Emergency rules (FR-S4) [D]
These do not depend on the blob. They are generous, so that Windows-parity behaviour never triggers them.

| Rule | Trigger | Recovery |
|---|---|---|
| Skin hot | any SAM skin channel the blob uses as a controller input ≥ 60 °C for 10 s | below 55 °C for 60 s |
| CPU hot | package temperature ≥ 105 °C for 10 s (TjMax is 100 °C on the SB3; the hardware already throttles there) | below 95 °C for 60 s |
| Core inputs lost | a controller input or the slider stays invalid for 60 s. Inputs this system does not provide from the start (SB3: base sensors 605/609) are logged at start and do not count; their controllers stay OFF | valid again for 10 s |

In emergency, firmware values are written and the engine keeps running in dry-run. The skin channels are the process values of
the blob's controllers that are temperatures; the CPU channel is the `coretemp` package temperature.

## 5. Foreign changes (FR-S5)
- **Detection:** before each write cycle, the output stage compares each owned target with the last value it wrote or read back. A difference beyond the hardware rounding (PL1/PL2/psys: 0.2 W) is a foreign change. A changed PL1 time window is one too.
- **First two changes within 60 s:** logged, then the own value is written again.
- **Third change within 60 s:** the supervisor yields. It restores firmware values once, writes nothing more, logs it and shows it in the status file.

## 6. Device profiles (FR-D3)
A profile is a small TOML file in `profiles/`. It holds no limit values (G-2). Fields:
- **Match:**
  - `match.product_name` and/or `match.sku` (one SKU or a list; the device's DMI `product_sku` must be one of them). At least one is needed;
  - `blob.source`: `efivar` or a file path (form C devices).
- **Engine and I/O:**
  - `engine.variant`: `classic` or `direct`;
  - `inputs.overrides`: channel → source, for example a different hwmon sensor;
  - `inputs.panel_max_nits`: optional;
  - `outputs.pl3`: `false` by default;
  - `outputs.fan_thresholds`: `false` by default; `true` lets the daemon send the fan, PROCHOT and charge-rate temperature thresholds (302-311, `outputs.md` 9). No shipped profile sets it;
  - `outputs.pl4_requires_ec`: `false` by default; `true` refuses control unless the EC inputs on the PL4 path are valid within `outputs.pl4_ec_wait_s` (default 10) of the start (`outputs.md` 3.2). Pro 10 only;
  - top-level `notes`: lines the report (`note: ...`) and the daemon log print for the device;
  - `outputs.narrow`: optional upper limits per target (FR-S7, only narrowing).
- **Status:** `tested`, `true` when the device was validated against Windows (NFR-1).

The SB3 15" profile is the only tested one in phase 4.

## 7. Suspend and resume (FR-E5, NFR-5)
The reference implementation uses only the Python standard library, so it does not listen on D-Bus. A systemd sleep hook
(`packaging/system-sleep/surface-power`) sends `SIGUSR2` before sleep and `SIGUSR1` after resume [D].
- **Before sleep:** `SIGUSR2` (logind `PrepareForSleep(true)` in a later version) → restore the firmware values, pause the loop.
- **After resume:** `SIGUSR1` →
  1. `engine.reset()`;
  2. request EC data (`inputs.md` 6);
  3. a fresh energy reference for power readings;
  4. continue.
- **Fallback:** without the hook, a `CLOCK_BOOTTIME − CLOCK_MONOTONIC` jump of more than 5 s counts as a resume [D]. In that case
  the firmware values are not restored before sleep.

## 8. Watchdog (FR-S3)
- **systemd watchdog:** `Type=notify`, `WatchdogSec=30`. The loop sends `WATCHDOG=1` once per cycle, only after a complete cycle, and also while paused for sleep. 30 s lies above the kernel's 20 s limit for freezing tasks: a suspend that hangs in the freezer (seen on the SB3 with a FUSE mount, 16.11.2026) stops the loop for up to 20 s [M].
- **Restart limits:** `Restart=on-failure` with `StartLimitBurst=3`, `StartLimitIntervalSec=600`. After that the unit stays failed and the device stays on firmware values. `OnFailure=thermald.service` is offered as an option in the unit, not set by default [D].

## 9. Main loop
Once per second:
1. Read the adapters.
2. Run `scheduler.step(now, inputs)`.
3. Apply the supervisor rules.
4. Apply the outputs, or log them in dry-run.
5. Update the monitor.
6. Notify the watchdog.

The loop sleeps until the next full second on `CLOCK_MONOTONIC`.
