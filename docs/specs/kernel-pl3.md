# Spec (concept): kernel module `surface_pl3`

Version 0.1, 23.11.2026. Concept only, to be detailed after the open points in section 5. Implements KM-1, KM-2, KM-4, KM-6 of `../requirements/kernel-modules.md`. Markers as in `kernel-mmio-rapl.md`.

## 1. Purpose
Write PL3 (output `0B7B5350/59`) from the kernel, so that it works under kernel lockdown (`integrity`, `confidentiality`), where user-space MSR writes are refused (`plan.md` D6). Separate module, separate patch from `surface_rapl_mmio`.

## 2. Windows target [C]
MSR `0x615`, bits 12:0, power units fixed at 1/8 W in the client, read-modify-write; enable (bit 15), clamp, time window and lock bits stay as read; no condition on CPU model or capability; every Intel platform type except Gemini Lake
(`research/10` 4.1). Measured: write, bind and restore on the SB3 (16.11.2026, `research/10-outputs.md` 4) and by testers on a Pro 9 (17.11.) and a Pro 10 (18.11.).

## 3. Interface (proposal, to decide)
No powercap constraint exists for PL3 and the core has no hook for it [H, to check]. Options: (a) a small platform device with one attribute (`power_limit_3_uw`, read/write, 0644) under `/sys/devices/platform/surface_pl3/`; (b) an extension of the powercap core (upstream-heavy, E5).
Recommended for the prototype: (a). Unit conversion with the power unit from MSR `0x606`.

## 4. Behaviour
- Opt-in per device (DMI table), same evidence rules as `kernel-mmio-rapl.md` 6, with the Windows client build and its table for channel 59 (`research/10` 4.1).
- Read-only unless `allow_write=1` (taint). Writes touch bits 12:0 only; whether MSR `0x615` is per package or per core must be checked before the write goes to one CPU [H].
- Use `rdmsr_safe`/`wrmsr_safe`: a faulting register (see section 5) fails the write with `-EIO` and the module disables itself after the first fault, with one log line.
- Record the firmware value at load; restore at unload.

## 5. Open points (before any code)
- **Pro 9: reads of `0x17`, `0x611`, `0x614`, `0x615` fail with EIO, also after a clean reboot and before the daemon starts [M, tester]** (`plan.md` F10): the cause decides whether the module is safe on that device.
- Where the attribute lives and whether the powercap core can take PL3 (E5).
- Interaction with the user-space daemon: the daemon would use the attribute when present, the MSR device otherwise.
