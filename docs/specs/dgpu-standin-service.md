# Spec: the dGPU stand-in under the daemon's unit (B15): clock helper and idle gate

Version 0.3, 26.11.2026 (0.1 draft; 0.2 with the maintainer's decisions of the same day, section 11; 0.3 after M1 and M2, section 9.1; 0.4 after M3, section 9.2; 0.5: gate and reporting implemented; 0.6: helper and daemon side implemented, section 13). **Installed on the SB3 26.11.2026 and tested under the units (10, 13.1 – 13.3); M4 done, M5 not pursued (maintainer, 26.11.2026).** Work package B15 of `../plan.md`. Changes how the stand-in of
`outputs.md` 6 (DO-1, `deviations.md`) reaches the GPU; target and control law stay as there. Also touches `inputs.md` 8.10 (dGPU sensors),
`supervisor.md` 2.5, 3, 7 and `monitoring.md`. Markers: [C] source code, [M] measured, [H] hypothesis, [D] design decision.

## 1. Problem
All [M] on the Surface Book 3 15", GTX 1660 Ti, driver 595.99.02, 26.11.2026 (`knowledge/sb3-test-practice.md`).
1. **The stand-in does not see the clients.** It talks to NVML only while another process holds `/dev/nvidia*`. With the unit's capabilities
   (`CAP_DAC_OVERRIDE CAP_SYS_RAWIO`) the search finds nothing while GNOME Resources holds the device; with `CAP_SYS_PTRACE` added it finds it.
2. **It could not set a cap either.** With the same capabilities NVML reads the power, and `nvmlDeviceSetGpuLockedClocks` returns 4 (no
   permission). Added one at a time, each of the other 38 capabilities fails; all capabilities except `CAP_SYS_ADMIN` fail; with
   `CAP_SYS_ADMIN` it works, also under the rest of the unit's sandbox.
3. **Effect:** over 7 min of load the computed limit fell from 50 to 15 W, no cap was set, the GPU drew about 62 W. Nothing in the log or the
   status file said so: no error, `cap_mhz` and `power_w` stayed empty.

The field test of 15.11.2026 that held 49.2 W (`research/08-field-tests-sb3.md`) ran the daemon as plain root from `scripts/field-test.py`, outside
the unit [C].

## 2. Goals and limits
- **G1** The stand-in acts under the shipped unit as it did in the field test.
- **G2** The daemon gets no new capability [D] (as decided for B11: no `CAP_SYS_ADMIN` for the daemon).
- **G3** The stand-in does not keep an otherwise idle GPU out of D3cold (as before).
- **G4** A stand-in that cannot act says so (log, status, series).
- Not a goal: a power limit (the SB3 driver refuses it, DV-5); other GPUs than the SB3's (`outputs.md` 6.1 stays).

## 3. Overview [D]
```
blob ─► output 201 (W) ─► surface-powerd (unchanged rights)
                            │  idle gate (section 4): may NVML be opened now?
                            │  NVML read: power, clock, utilisation
                            │  control law (outputs.md 6)
                            └─ "cap 1290" / "reset" ──► surface-gpu-clockd (CAP_SYS_ADMIN) ──► NVML locked clocks
```
Reading stays in the daemon: power, clock, utilisation and temperature are readable under its unit's capabilities and sandbox [M, 9.2]. Only the two
privileged calls move into a separate service.

## 4. Idle gate: no search for clients [D]
It replaces `ClientProbe` for the stand-in and for the dGPU sensors. `CAP_SYS_PTRACE` is needed nowhere.

**Premise [M, 9.1]:** sampling keeps an otherwise idle GPU awake, in one open session as with a new session per sample. An open session without further queries does not.

**Why not the base temperature (considered 26.11.2026):** only controller 4 reads sensor 609. The slider, posture, adapter and budget branches limit
the dGPU with a cold base (SB3: 50 W on AC, 25 W lid closed, 35 W battery saver) [C blob, M]. In the run of 26.11. the 50 W limit applied from the
first second, controller 4 was READY after 2 min.

| State | Entered | NVML | Leaves to |
|---|---|---|---|
| **asleep** | `power/runtime_status` reads `suspended` | closed, never opened | **sampling** at once when the status is no longer `suspended` |
| **sampling** | GPU awake | one session, a sample every 2 s | **quiet** after `IDLE_SAMPLES` idle samples in a row; **asleep** |
| **quiet** | idle in sampling | closed | **asleep** when the GPU suspends; **sampling** after `QUIET_MS` if it is still awake |
| **gone** | no `power/runtime_status` (base detached) | closed | **sampling** or **asleep** when a device is back |

- Reading `power/runtime_status` needs no capability and does not wake the GPU and does not keep it awake [M, 9.1]. It is read every daemon cycle.
- **Close before the pause, then no NVML call at all.** In M2b the GPU slept with the session still open, and the later `nvmlShutdown` woke it again [M]. The session is therefore closed when **quiet** is entered, while the GPU is awake; nothing touches NVML in **quiet** and **asleep**.
- **Idle sample:** GPU utilisation ≤ `IDLE_UTIL` %, and power below `T − 2 W`. A sample above the target is never idle.
- **Values:** `QUIET_MS` = 35 000 (sleep delay after the last query at most 21.5 s [M], margin above 50 %). First values [H, M5]: `IDLE_SAMPLES` = 5, `IDLE_UTIL` = 1.
- **gone:** the daemon forgets its cap (a re-added device is treated as uncapped [H, M4]), looks the PCI address up again, and does not disable
  the stand-in.
- **dGPU sensors** (`inputs.md` 8.10): asleep reads 0 W with an invalid temperature (as today); quiet keeps the last sample; gone is invalid.

**What this accepts:**
- A load that starts during **quiet** on a GPU that never slept runs for at most `QUIET_MS` with the cap as it is: the last one, or none if
  none was set since the driver was loaded.
- A new target (lid closed, slider) reaches a sleeping or quiet GPU at the next sample. The cap stays in force across D3cold [M, `research/08`].
- A program that only holds the device (GNOME Resources) no longer makes the daemon sample forever. Whether the GPU then sleeps is up to that program.
- Work that shows no GPU utilisation (video decode or encode alone [H]) counts as idle only while its power is below `T − 2 W`.

## 5. The clock helper `surface-gpu-clockd` [D]
### 5.1 Task
Set or remove the graphics-clock cap of the first NVIDIA display controller. Nothing else: no blob, no EC, no sysfs writes, no network, no
configuration file, no measurement, no control law. Standard library and the NVML wrapper only.

### 5.2 Interface
- Unix stream socket `/run/surface-gpu-clock/sock`, owner root, mode 0600, created through systemd socket activation (`surface-gpu-clockd.socket`).
- The peer must have UID 0 (`SO_PEERCRED`). Root processes cannot be told apart; the socket does not claim to admit only the daemon.
- One request per connection, one line of ASCII, at most 32 bytes; one answer line. Anything else is refused and the connection closed.

| Request | Action | Answer |
|---|---|---|
| `ping` | none; does **not** open NVML (never wakes the GPU) | `ok 1` (protocol version) |
| `cap <mhz>` | decimal integer; refused below 300 or above the GPU's maximum graphics clock; sets the locked-clock range 300 … mhz | `ok` |
| `reset` | removes the locked clocks | `ok` |
| any failure | – | `error <kind> <text>` with `kind` one of `request`, `range`, `permission`, `no-gpu`, `nvml` |

- NVML is opened for the one call and closed. Requests are served one after another; an idle connection is dropped after 2 s.
- The helper can only lower the clock or give it back. It cannot raise a clock or power limit above the driver's own.
- It logs every refused request and every NVML error; successful caps are not logged one by one.

### 5.3 Unit
Measured as a whole on the SB3 [M, 9.2]; with exactly these lines the cap can be set and removed:
```
CapabilityBoundingSet=CAP_SYS_ADMIN
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@mount @module @reboot @swap @debug @raw-io @obsolete
DevicePolicy=closed
DeviceAllow=/dev/nvidia0 rw
DeviceAllow=/dev/nvidiactl rw
InaccessiblePaths=-/sys/firmware/efi/efivars -/dev/surface
MemoryMax=64M
```
- `CAP_SYS_ADMIN` alone is enough; `CAP_DAC_OVERRIDE` and `CAP_SYS_RAWIO` are not needed [M].
- **The helper runs as root.** With `DynamicUser=yes` and `CAP_SYS_ADMIN` as an ambient capability the call returns 4 [M]; why is not shown [H].
- The socket server is not part of the measurement; `AF_UNIX` stays allowed. M3 shows that the lines are compatible with NVML; that each of them
  takes effect is not tested here. The device test (10) checks efivars, `/dev/surface` and other device nodes from inside the unit.
- Other NVIDIA devices, driver versions and distributions may need other lines: a failing call is reported (6, 7), not silent.

**Remaining risk, stated plainly:** `CAP_SYS_ADMIN` is very broad. A flaw that lets an attacker run code inside the helper is serious despite the
sandbox. The protection is the size of the code and of its input: one short line from root only. A daemon with `CAP_SYS_ADMIN` would instead
hold it next to the blob parser, the EC transport and all other outputs.

## 6. Daemon side [D]
- **Start:** with the stand-in on, the daemon sends `ping`. Without a helper the stand-in is off for this run, with one warning that names the
  reason; the dGPU limit then counts as not written. The other limits are controlled as with a missing dGPU input (`supervisor.md` 2.5).
- **Setting:** the control law sends `cap` only when the value changes, and `reset` where `outputs.md` 6 releases. Time-out 1 s per request; the
  loop must stay well inside the watchdog time.
- **Errors:** `permission`, `request`, `range`: the stand-in is off for the rest of the run (a defect or a setup fault). `no-gpu`, `nvml`, socket
  errors and time-outs: logged, tried again at the next sample; off after 5 in a row. A device that goes to **gone** resets the count.
- **Stop and crash:** before the first `cap` the daemon writes a marker `gpu-cap` into its run directory and removes it after a successful
  `reset`. `surface-power-reset` sends `reset` if the marker exists, so a killed daemon leaves no cap behind. (Today the cap is not part of the
  snapshot [C]; the case never occurred because no cap was set under the unit.)
- **Sleep (changed 26.11.2026 after M4):** nothing is sent and NVML is not called before sleep: the daemon hears of it after
  `nvidia-suspend.service`, and a call then hangs until the resume [M, 13.2]. The cap stays set (it can only lower the clock). After the
  resume the next sample sends the cap, or its removal, again whether or not the value changed. Open [H]: a sample that is due between
  `nvidia-suspend.service` and the signal could still block the loop.
  The loop that could block in this way is paused before `nvidia-suspend.service` since B16 (`supervisor.md` 7): five of five cycles on the SB3
  passed on 26.11.2026, two of them right after a GPU load [M].
- **Release failures:** only a permanent error (6, errors) or five in a row end the stand-in; until then the cap stays remembered and marked.
- **Dry-run and observe-only** (`outputs.md` 6.2): the gate and the readings as above; nothing is sent to the helper, `ping` excepted.

## 7. Saying that it does not act (G4) [D]
- Status `dgpu`: `gate` (asleep, sampling, quiet, gone), `helper` (ok or the error), `cap_mhz`, `power_w`, `target_w`, `acting` (false with a reason).
- The output's target text says "clock-cap stand-in, off: <reason>" when it cannot act.
- Time series: columns `dgpu.gate`, `dgpu.cap_mhz`, `dgpu.power_w`. The two soak tests could not show the fault because the series records only
  the computed limit.
- Warning, once per run: in sampling, `P > T + 5 W` for 30 s although a cap was sent ("the cap has no effect").

## 8. Packaging
`surface-gpu-clockd.service` and `.socket` next to the daemon's units; the daemon's unit gets `Wants=` and `After=` on the socket. The helper is
installed and enabled only for the stand-in; without an NVIDIA GPU its socket stays unused. The daemon's `CapabilityBoundingSet` is unchanged.
The dry-run unit needs no helper.

## 9. Measurements before implementing (SB3, each with the maintainer's yes)
| ID | Question | How | Needs |
|---|---|---|---|
| M1 | How long from the last NVML session to D3cold? | no other holder (GNOME Resources closed), one sample, then only `runtime_status` and `power_state`; 10 times, with `nvidia-persistenced` as installed (`--no-persistence-mode`) | no sudo |
| M2 | Does sampling every 2 s keep an idle GPU awake? Does reading `runtime_status` alone wake or hold it? | as M1 with each reader | no sudo |
| M3 | The helper's smallest unit: `CAP_SYS_ADMIN` alone, then each sandbox line; the daemon's unit: utilisation and clock readable | transient units, as `gpu-takt-rechte.py` | sudo |
| M4 | Does a cap survive system sleep, and detach plus re-attach? | set, cycle, read the clock under load | sudo |
| M5 | Utilisation and power under idle desktop, video playback, encode alone and CUDA load | sample log | no sudo — **not pursued** (maintainer, 26.11.2026: video and light desktop loads run on the iGPU); `IDLE_SAMPLES` and `IDLE_UTIL` stay [H] |

M1 and M2 decide the gate. They are done (9.1): sampling keeps the GPU awake, so **quiet** is needed.

### 9.1 Results of M1 and M2 (SB3 15", 26.11.2026 14:40 – 14:52, driver 595.99.02)
No other holder, `power/control` = `auto`, read-only, NVML used from one process as in the daemon; sysfs polled every 0.5 s (raw
`c6-2026-11/results/gpu-schlaf-*.log`). All [M].
| Case | Result |
|---|---|
| reading `runtime_status` only, 60 s, GPU asleep | stayed `suspended` throughout |
| M1: one session, one sample, closed (10 times) | `suspended` again after 21.0 – 21.5 s (median 21.0) |
| M2a: one open session, a sample every 2 s, 120 s | never `suspended`; `suspended` 8.5 s after the end |
| M2b: open session, one sample, then nothing for 120 s | `suspended` after 21.5 s with the session open (82 % of the window); after closing it, 21.5 s again |
| M2c: a new session every 2 s, 120 s | never `suspended`; 9.5 s after the end |

- A sample on a sleeping GPU takes 0.93 – 1.04 s (wake included). Its power reading was 1.0 W each time; while polled, the idle GPU read a median of
  3.2 – 3.4 W, at most 18.9 W, at 300 MHz after the first samples. Utilisation read 0 % in every idle sample.
- Why the delay after continuous sampling (8.5 – 9.5 s) is shorter than after a wake (21 s) is not shown [H].
- Not covered: another program holding the device, and utilisation under light loads (M5).

### 9.2 Results of M3 (SB3 15", 26.11.2026 15:01, driver 595.99.02) [M]
Transient units, each probe: set a 1500 MHz cap and remove it (raw `c6-2026-11/results/gpu-helfer-unit-*.txt`, script `gpu-helfer-unit.py`).
- Control with all rights: ok. `CapabilityBoundingSet=CAP_SYS_ADMIN` alone: ok.
- Each of 25 sandbox settings alone: ok. All together: ok. None had to be dropped.
- `DynamicUser=yes` with ambient `CAP_SYS_ADMIN`, plus that set: `nvmlDeviceSetGpuLockedClocks` returns 4.
- The daemon's capabilities and sandbox: power, clock, utilisation, temperature and default power limit readable.

## 10. Tests
**Fake sysfs, fake NVML, fake helper socket** (written first):
- Gate: no NVML while suspended; sample at once on wake; quiet after `IDLE_SAMPLES`; no NVML during quiet; back after `QUIET_MS`; a sample above
  the target is never idle; gone forgets the cap and does not disable the stand-in.
- Helper protocol: every refusal of 5.2 (length, garbage, range, second line, peer not root); `ping` opens no NVML; one call per request.
- Daemon: no helper → stand-in off, reason in log and status, control of the other limits; error kinds of 6; marker and reset tool;
  warning "the cap has no effect"; series columns.

**On the device, under the installed units** (not as plain root): a scripted GPU load; pass if the mean power of the last 5 minutes is within
2 W of the target, the cap is gone after a stop and after `SIGKILL` of the daemon, and the GPU reaches D3cold after the load with no other
holder. **Rule [D]:** an output counts as "checked on the device" only after a test under the shipped unit.

## 11. Decisions of the maintainer (26.11.2026)
| # | Question | Decision |
|---|---|---|
| O1 | Who holds the right to set the cap? | A helper service, so that the capabilities stay as narrow as possible (5.3). The daemon gets no new capability. |
| O2 | Idle gate instead of the client search? | Yes, if M1 and M2 allow it. |
| O3 | No helper available? | Only the stand-in is off; the CPU and psys limits stay enforced. |
| O4 | A killed daemon | Marker and reset tool (6). |
| O5 | `dgpu_clock_cap` in the SB3 profile until the fix | Stays on; the deviation records that it does not act under the unit. The fix is the next work. |

## 12. Order of work
M1, M2 → M3 → tests → gate and observability (usable in dry-run without the helper) → helper and daemon side → M4, M5 → device test.

## 13. Implementation notes (0.5, 26.11.2026)
Implemented: the gate (section 4, `adapters/gpu.py` `GpuGate`, shared by the stand-in and the dGPU sensors; `ClientProbe` and the `/proc` scan are
removed), the reporting of section 7 and the series header rule (`monitoring.md` 3). Tests on fakes: `tests/test_gpu.py` (gate, stand-in, sensors),
`tests/test_daemon.py`, `tests/test_base_detach.py`.
- **Helper (0.6):** `gpuclockd.py` (entry point `surface-gpu-clockd`), client `adapters/gpuclock.py`, units
  `packaging/systemd/surface-gpu-clockd.service` and `.socket`; the daemon's unit gains `Wants=`/`After=` on the socket and no capability.
  Tests `tests/test_gpuclockd.py` (protocol, refusals, error kinds, stand-in with the real server and a fake NVML, marker, reset tool, unit
  files against 5.3).
- **Where the code settles a point:** the helper's socket carries one request per connection, a silent client is dropped after 2 s in total; the
  peer must be root (run by hand without root, also the own user, who cannot set anything). `ping` has a time-out of 5 s (it starts the helper),
  other requests 1 s. The daemon pings only in control mode and not for observe-only; a marker left by a predecessor is cleared then. When the
  stand-in gives up while a cap is set, it tries once to take it back. The marker is `<run dir>/gpu-cap`; `surface-power-reset` exits with 1 and
  keeps it if the helper does not answer, and still restores the snapshot. A stand-in without a setter (library use, tests) still calls NVML
  itself; the daemon always uses the helper, also when run as root (`scripts/field-test.py` therefore needs the installed socket now).
- **Daemon without a helper (O3):** the stand-in stays the output's path, is switched off with the reason (`dgpu.helper`, `dgpu.reason`, the
  output's target text, one warning); control of the other limits is unchanged.
- **Session on a sleeping GPU:** if the GPU suspends with a session open, the gate leaves it open and sends nothing (closing would wake the GPU [M, 9.1]).
- **Pause:** the session is closed one daemon cycle after the fifth idle sample, so that every consumer of that sample can still use it.
- **Errors:** NVML codes 3 (not supported) and 4 (no permission), a missing library and the helper's `permission`, `request`, `range` end the
  stand-in at once; other errors (`no-gpu`, `nvml`, an unreachable helper) after 5 in a row.
- **Log:** the first cap of a run is logged once (an addition to 5.2 and 7); `status.json` has `dgpu.gate`, `dgpu.acting`, `dgpu.reason`,
  `dgpu.helper`.
- **On the SB3** [M, 26.11.2026, dry-run of this code as the user, not under a unit]: with the GPU asleep, 75 s without waking it; after one
  foreign query: sampling, pause after 9 s, GPU `suspended` after 21.5 s while the daemon ran. No cap path was exercised.
- **Helper on the SB3** [M, 26.11.2026, run by hand as the user]: `ping` left the GPU in D3cold; socket mode 0600; `cap 1500` and `reset`
  answered `permission` (NVML 4), `cap 100` answered `range`. Setting a cap through the installed helper unit is not tested yet.

### 13.1 Test under the installed units (SB3 15", 26.11.2026 15:25 – 15:33) [M]
Wheel `4da67f71…`, units as installed by `raw/…/b15-installation-2026-11/install-b15.sh`; script `b15-geraetetest.py` (first run).
- Daemon `CapEff` unchanged (`CAP_DAC_OVERRIDE`, `CAP_SYS_RAWIO`), helper only `CAP_SYS_ADMIN`; inside the helper's mount namespace `/dev/surface`
  and efivars are empty. systemd lists `char-rtc r` besides the two NVIDIA nodes in the helper's `DeviceAllow` (added by `ProtectClock` [H]).
- **The cap works under the units:** first cap 1320 MHz within 5 s of the load. Under 7 min of CUDA + NVENC load at slider 5 (target 50.0 W):
  power over the last 5 min mean 49.14 W (47.3 – 50.6 W, mean deviation −0.87 W), never above target + 2 W after the first minute; cap
  1185 – 1395 MHz. Without a cap the same load drew about 62 W (earlier the same day).
- The base reached 46.7 °C; controller 4 (ready from 46.95 °C at slider 5) stayed OFF: **its closed loop with an acting limit is still untested.**
- The run was aborted after 7 min by the script's CPU limit of 97 °C (short spikes between 84 and 98 °C, set too tight; now as in
  `scripts/field-test.py`). After the load ended the gate reported `asleep` with the daemon running and the cap kept (marker present), as designed.
- **Second run, 15:35 – 15:41** (`--kurz`: 3 min load, then phases 3 – 5), all eight checks passed:
  - daemon stopped under load: the GPU went back to 63.7 W at up to 1530 MHz (48.9 W at a 1275 MHz cap before), no marker left;
  - `SIGKILL` under load: `surface-power-reset` logged `GPU clock cap removed` 0.12 s after the kill; systemd restarted the daemon, which set
    a cap again at once (1230 MHz);
  - load ended with no other holder: gate `quiet` after about 12 s, GPU `D3cold` after 18 s with the daemon running.
- Controller 4 became READY at 47.0 °C (slider 5, U 57 W, above the 50 W table, so not binding) and never ACTIVE (47.95 °C): **its closed
  loop with an acting limit stays untested.**
- Side observation, not part of B15: the unit installed on this machine has `OnFailure=thermald.service` (commented out in the packaged unit).
  The single `SIGKILL` triggered it although `Restart=on-failure` followed: thermald ran for about 7 s until the restarted daemon's `Conflicts=`
  stopped it. What it wrote in that time was not recorded.
- **To be covered by M4:** while the gate samples, the daemon holds `/dev/nvidia*` for up to about 10 s after any wake; a detach handler that
  refuses a GPU with holders would name it then.

### 13.2 Controller 4, system sleep and detach (SB3 15", 26.11.2026 15:49 – 16:14, wheel `4da67f71…`) [M]
Script `b15-regler4-standby-abloesen.py`.
- **Controller 4 with an acting limit** (profile `balanced`, set point 44.95 °C, full CUDA + NVENC load): ACTIVE from 44.95 °C after 3 min;
  the base peaked at 46.15 °C and in the last 5 of 12 min held 45.2 °C on average (44.95 – 45.65); the limit settled at 41.9 – 42.5 W, the
  GPU followed (mean 0.68 W below the limit, cap 1110 MHz). Without an acting cap the same morning the base rose past 52 °C with the limit
  at 15 W.
- **System sleep (36 s s2idle): failed.** The release before sleep hung (the hook runs after `nvidia-suspend.service`), the daemon logged
  `paused` only after the resume, so **no output was restored before sleep**; the request timed out and the stand-in switched itself off for
  the rest of the run. Fixed in the code after this run (6, "Sleep" and "Release failures"), not yet re-tested.
- **Detach and re-attach** (stand-in already off, so the daemon did not touch the GPU): no emergency, base sensors absent and back
  (`hwmon8`); the detach handler passed twice and `sdtxd` logged `unexpected disconnect: latch is closed` again. After the re-attach the GPU
  stayed in `D0` for the 3 min observed, with no process holding it: `/proc/driver/nvidia/gpus/…/power` read **`Runtime D3 status: Not
  supported`** (after a boot: `Enabled (fine-grained)`), `power/control` `auto`, `runtime_usage` 1. **The D0 block after a re-attach is
  therefore independent of the daemon**; why the driver drops runtime D3 on the re-added device is not shown [H].
- **Still open:** system sleep and detach with the fixed version.

### 13.3 Re-test of system sleep and detach (SB3 15", 26.11.2026 16:27 – 16:37, wheel `decb1204…`, after a reboot) [M]
- **System sleep (36 s s2idle):** the stand-in stayed ready, the cap (1380 MHz) stayed remembered and marked; under load afterwards 48.3 W at a
  50.0 W target; the GPU reached D3cold 19 s after the load. No error from the stand-in. **M4, sleep: passed for the stand-in.**
- **Detach and re-attach:** no emergency, no cap remembered while detached, and after the re-attach regulated again (50.4 W at 50.0 W).
  **M4, detach: passed.** After the re-attach the GPU stayed in D0 again with `Runtime D3 status: Not supported`; the gate then sampled for about
  8 s every 44 s (a holder in 17 of 87 checks), as designed for an awake, idle GPU.
- **Not part of the stand-in, found here:** the daemon logged `paused` only after the resume again, without having touched the GPU: the restore
  before sleep did not happen (`supervisor.md` 7, plan B16).

