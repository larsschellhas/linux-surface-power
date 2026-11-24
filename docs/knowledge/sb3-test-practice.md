# Hardware tests on the Surface Book 3 15" (reference device)

24.11.2026, from the module tests of 23.–24.11.2026 (raw note `raw/arbeitsordner/km-sb3-test-2026-11-23.md`). Applies to this machine; other devices need their
own checks. Every write test needs the maintainer's yes before each step, and `sudo` runs only in a terminal tab the maintainer types into (`CLAUDE.md`).

## Before a test
- **Record and stop the writers** (step 0 of `../specs/kernel-mmio-rapl.md` 8): `surface-powerd` (its `ExecStopPost` restores the snapshot), power-profiles-daemon,
  thermald (disabled here), tuned, `surface-rapl.service`. Write the states into the result file; start them again afterwards.
- **power-profiles-daemon comes back by itself:** GNOME's settings start it again over D-Bus (seen 28 s after a stop, 24.11.2026). Stop it and
  `systemctl mask --runtime power-profiles-daemon`, unmask at the end.
- **Record and set the CPU state:** `platform_profile`, EPP, governor, `scaling_min_freq`/`scaling_max_freq`. A run under `low-power` (EPP `power`) gave a control
  value close to the test limit and was useless as evidence [M]. Set `performance` and restore the old values at the end.
- **Choose limits below ~10 W** for tests without the daemon: the psys firmware limit holds the package at ~13–15 W anyway
  (`rapl-power-limits.md` 5).

## The module
- Secure Boot is on with `module.sig_enforce` = Y (lockdown `none`): an unsigned module is refused. A copy signed with the machine's own DKMS key loads; no change of the
  MOK list or of Secure Boot is needed.
- The scratchpad under `/tmp` is cleared at every boot: the signed test build has to be built and signed again (maintainer's yes).
- The module has no alias and is never loaded at boot. The daemon chooses its PL path at start, so after loading the module the daemon must be restarted to use the zone.
- The kernel's taint flag `S` is already set on this machine (MSR writes from user space), so a taint caused by the module cannot be shown here.

## Suspend
- Use `rtcwake -m no -s 40` and then `systemctl suspend`. `rtcwake -m freeze` bypasses systemd; the nvidia driver then refuses the suspend (`nv_pmops_suspend …
  returns -5`) and the cycle is not valid [M].
- A cycle is valid only with `PM: suspend entry (s2idle)` and `PM: suspend exit` in the kernel log and no "failed to suspend". Search the log from a **unique marker**
  written to `/dev/kmsg` with a timestamp; an old marker from an earlier run matched stale error lines once.

## Measuring
- Package power from `intel-rapl:0/energy_uj` (it is the same counter as the MMIO zone's, checked +24.18 J in 5 s in both [M]), platform power from
  `intel-rapl:1/energy_uj` (psys), frequency as the mean of `scaling_cur_freq`, temperature from `x86_pkg_temp`. 1–2 s samples, mean of the tail of each cell.
- Save every result file and every reading taken after a reboot into the test folder; a value that exists only in a chat or a note cannot be re-checked.

## Daemon sandbox
- Scripts run in a transient unit with the daemon's sandbox must not lie in `/tmp` (`PrivateTmp` hides them).
- The daemon's own MMIO check does not work under its units (B11); read the copies with `sudo python3 scripts/mmio-pl-read.py` or `surface-power-report`.
