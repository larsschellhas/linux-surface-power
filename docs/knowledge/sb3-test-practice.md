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

## Kernel and the daemon's mode
- The module is built for one kernel: after a kernel update (7.3.4-2 → 7.3.6-1-surface on 24.11.2026) it has to be rebuilt and signed again.
- A write test through the daemon needs the daemon in control: if an input that reaches a written output is missing at start (the base sensors 605/609
  after the prototype modules were removed, 24.11.2026), it falls back to dry-run and writes nothing. Check the mode in the journal before the
  measurement; a script must stop when the expected register value is not there (the warm-restart check of 24.11.2026 was lost that way).
- Result files written by a script run with `sudo` belong to root; corrections go into a separate file.

## Daemon sandbox
- Scripts run in a transient unit with the daemon's sandbox must not lie in `/tmp` (`PrivateTmp` hides them).
- The daemon's own MMIO check does not work under its units (B11); read the copies with `sudo python3 scripts/mmio-pl-read.py` or `surface-power-report`.

## GPU load and skin controller 4 (26.11.2026)
- Load without sudo, with the NVIDIA GPU at 99 %: `ffmpeg -init_hw_device cuda=cu:0 -filter_hw_device cu -f lavfi -i testsrc2=size=1920x1080:rate=60 -vf "format=yuv420p,hwupload_cuda,bilateral_cuda=sigmaS=10:sigmaR=0.1:window_size=21,bilateral_cuda=sigmaS=10:sigmaR=0.1:window_size=21" -c:v hevc_nvenc -preset p7 -f null -`.
- With the base at 32.6 °C, profile `balanced` on AC and no clock cap: base sensor `2E8B14C2/609` READY after 2.0 min (44.4 °C), ACTIVE after 2.1 min (45.1 °C), 52.2 °C after 7.2 min; display skin sensor 509 stayed at 35.6 - 37.2 °C [M].
- A test abort limit for the CPU must allow short package spikes up to TjMax: under CUDA + NVENC load samples jumped between 84 and 98 °C [M, 26.11.2026]; use the rule of `scripts/field-test.py` (105 °C for 15 s).
- The GTX 1660 Ti regulates itself to its target temperature of 87 °C (slowdown 95, shutdown 100 °C, `nvidia-smi -q`): a test abort limit must lie above 87 °C [M].
- GNOME Resources holds `/dev/nvidia*` open and keeps the GPU in `D0`; a D3cold check says nothing while it runs [M]. `nvidia-smi` wakes the GPU as well.
- **When the idle dGPU sleeps [M, 26.11.2026, no other holder]:** one NVML sample, closed: `suspended` after 21 s (10 of 10). Sampling every 2 s keeps it awake, in one session or with a new one each time (idle power then about 3.3 W by NVML). An open session without queries lets it sleep, but `nvmlShutdown` on the sleeping GPU wakes it. Reading `power/runtime_status` neither wakes nor holds it.
- Setting a cap works with `CAP_SYS_ADMIN` alone under a tight sandbox, as root only (`DynamicUser` with the ambient capability: NVML 4) [M, 26.11.2026, `specs/dgpu-standin-service.md` 5.3].
- Since the idle gate (26.11.2026) the daemon no longer looks for GPU clients; the findings below describe the version before (551631c). Test of the new version under the installed units: `specs/dgpu-standin-service.md` 13.1.
- `nvidia-suspend.service` wakes a sleeping dGPU (kernel: `Enabling HDA controller`) before it suspends the driver; a program that queries NVML when it sees the GPU awake can get stuck there. Whatever has to pause for a sleep is ordered `Before=nvidia-suspend.service` [M for the wake, 26.11.2026].
- The sleep hook (`SIGUSR2`) runs **after** `nvidia-suspend.service`: an NVML call made then hangs until the resume and holds up whatever the daemon does next [M, 26.11.2026]. Nothing before a system sleep may call the NVIDIA driver.
- Check every sleep cycle for the daemon's `paused` line **before** `System returned from sleep operation`: otherwise nothing was restored before sleep (plan B16) [M, 26.11.2026]. Python's `time.sleep` goes on after a signal handler: a loop that has to react to a signal at once waits on `signal.set_wakeup_fd`.
- A unit with `OnFailure=thermald.service` starts thermald after a single kill although `Restart=on-failure` brings the daemon back; thermald then ran for about 7 s [M, 26.11.2026].
- The stand-in set no cap in this test (plan B15). Cause: without `CAP_SYS_PTRACE` a root process cannot read the `/proc/<pid>/fd` links of a user's processes; with the unit's capability set the client search returned nothing, with `CAP_SYS_PTRACE` it found the holder [M, 26.11.2026]. Check anything the daemon reads about other processes under its unit, not as plain root (the same trap as B11). Second barrier: under the same capability set `nvmlDeviceGetPowerUsage` works, `nvmlDeviceSetGpuLockedClocks` returns 4 (no permission) [M, 26.11.2026]. The call needs `CAP_SYS_ADMIN`: no other single capability and not all others together suffice (driver 595.99.02, every capability tried, 26.11.2026) [M]. The successful stand-in test of 15.11.2026 (`research/08`, 49.2 W mean) ran the daemon from `scripts/field-test.py` as plain root, with every capability, not under a unit [C script]; the unit has limited capabilities since its first version (14.11.2026) [C git]. A field test run as root does not cover the service.

