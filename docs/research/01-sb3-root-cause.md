# Surface Book 3: why it throttles on Linux

## Symptom
Under sustained load on Linux, the SB3 15" holds the package at a few watts below what Windows allows. System power plateaus at ~25 W,
including the display backlight ([kernel#117](https://github.com/linux-surface/kernel/issues/117)).

## Cause [M]
- The firmware sets **psys (platform power, MSR 0x65C, `intel-rapl:1`) to PL1/PL2 = 25/40 W** at boot. These values never change at runtime:
  not on AC/battery changes, not on suspend/resume.
- psys is not locked. Raising it via `intel-rapl:1` takes effect immediately.
- Package limits from the firmware are PL1/PL2 = 30/61 W, with a 28 s time window.
- There is no DPTF table in the ACPI; only CPU, SA, TCSS, xHCI, RTD3 and TPM SSDTs are loaded.

## Why Windows doesn't throttle [C, M]
Windows' Surface SMF sets psys dynamically:
- psys PL1 = available input power (from the EC) + 23 W;
- psys PL2 = available input power + 34.5 W.

PL1 is additionally capped by a value switched by PSU size (around 45 W). With the Surface PSU, psys did not bind in any Windows measurement. Instead, SMF regulates the **package PL1 by the skin
temperature** of the display part (EC sensor `I_VTS1`). See 03.

## Power sources [M]
- The EC reports available input power per part in BAT event/request `0x53`:
  - display part (SAM): 45.7 W on battery, 73.5 W with the Surface PSU;
  - base (KIP): 65 W with USB-C PD only, 119.3 W with the 127 W PSU.
- With USB-C 65 W only and GPU load, the base battery drains (~29 W at psys 25 W, ~34 W at 30 W) on Linux **and** on Windows.
  The GPU (~62 W on Linux) is the main consumer. Raising psys adds only ~5 W.
- On Windows the dGPU is held at 49.64 W with the Surface PSU, 65 W with USB-C only, and 31.7–50.3 W on battery. On Linux nothing limits it.

## Related findings
- The SB3 EC temperature sensors (`I_RTS1–8`, `I_VTS1`) and the fan speed are not registered by mainline. A two-line registry patch is
  submitted as [kernel#190](https://github.com/linux-surface/kernel/pull/190). Until it lands, an out-of-tree module (DKMS) exposes them as
  hwmon `surface_thermal` (`temp9` = `I_VTS1`) and `surface_fan`.
- The FAN commands 0x0e and 0x0f have no effect on the SB3; the fan is EC-controlled.
- thermald (Ubuntu, `--adaptive`) lowers package PL1 by itself at high temperatures. It works against any other controller and has to be
  stopped while ours runs.

Evidence: `raw/arbeitsordner/Ergebnis-psys-Test.md`, `kommentar-117*.md`, `psys-*.log`, `entladung-test.log`, `ssam-0x53*.log`.
