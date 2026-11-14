# Prototype and related work

## Existing prototype (`raw/arbeitsordner/dienst/`, Python)
Built before the SMF analysis. It reproduces the Windows behaviour from measurements, not from the blob:
- **Controller V2:** P on the measured skin value, I on a predicted skin value, a CPU target of 75 °C, anti-windup, a TjMax guard
  (PL1+PL2 → 21 W), and a profile table per `platform_profile`.
- **Service structure, reusable as is:**
  - states regulate / fallback (VTS1 missing → PL1 15 W) / emergency (firmware values) / off;
  - sensor plausibility checks (10–70 °C, jumps > 3 K/s rejected);
  - detection of foreign PL1 changes and of resume (`CLOCK_BOOTTIME`);
  - `sd_notify` with a watchdog, `ExecStopPost` reset from `original.json`;
  - daily CSV, `status.json`, and a user notifier (battery draining on AC, state changes).
- **Tests:** 13 simulation scenarios with 32 checks, all passing. A probe run on the device restored the firmware values after a clean end
  and after `kill -9`.
- **Known deviations from SMF:**
  - the CPU target of 75 °C (SMF has none);
  - the skin target of 45 °C (SMF: 48 °C);
  - a warm start at 12 W instead of the fixed cut-in;
  - psys fixed per profile instead of from the available power.

The new implementation replaces the controller with the SMF engine and keeps the service structure.

## Related projects
- [ikegam/surface-ultra-misc](https://github.com/ikegam/surface-ultra-misc) (`thermal/`), Surface Laptop Ultra, MPTF:
  - `n1x-thermald` uses a PI loop on VTS1 (45 °C) and a secondary skin sensor (51 °C) and acts through `scaling_max_freq`;
  - an extra ACPI thermal zone is the Tj safety net.
  
  Same principle, other framework; see linux-surface#2384.
- [linux-surface#2328](https://github.com/linux-surface/linux-surface/issues/2328) (Laptop Studio): `SGPC._DSM`/`SACT` gate a 10 W dGPU emergency
  limit after D3cold. The SB3 `SGPC` has no `_DSM` or `SACT`; it only receives on/off notifications. A different mechanism.
- [besiple/surface-laptop-studio-dgpu-fix](https://github.com/besiple/surface-laptop-studio-dgpu-fix): the `acpi_call` fix for #2328.
- thermald: not suitable for SMF behaviour (no EC skin input, own PL1 changes); see intel/thermal_daemon#616.

## Upstream threads
- [linux-surface#2383](https://github.com/linux-surface/linux-surface/issues/2383): tracking issue for SMF, with the related reports per device.
- [linux-surface#2384](https://github.com/linux-surface/linux-surface/issues/2384): MPTF placeholder.
- [kernel#117](https://github.com/linux-surface/kernel/issues/117): SB3 psys, measurements, change of approach.
- [kernel#190](https://github.com/linux-surface/kernel/pull/190): SB3 sensor and fan nodes.
- Collection of org-wide references: `raw/arbeitsordner/recherche/querverweise-organisation.md`.
