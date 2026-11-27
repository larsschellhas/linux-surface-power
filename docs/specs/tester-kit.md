# Spec: tester kit (one command for a device check)

Version 0.5, 27.11.2026 (0.5: build steps K1–K6 implemented; where the build differs from the design of 0.4, the text below says what is built).
**State:** implemented and tested on a simulated machine (`tests/test_kit_run.py`, `test_kit_parts.py`, `test_check_eval.py`, `test_module_guard.py`).
On the Surface Book 3 without root: the module builds against 7.3.6-1-surface, the replay of `check-eval.py` equals the running daemon's six computed
limits, and the kit's start up to its last question behaves as described [M, 27.11.2026]. **With root on the Surface Book 3, 27.11.2026 [M]:**
S1–S5 all passed in one run (S2: package power steady at 11.37 W at the platform limit 25 W; S3: zone equals the register; S4: unchanged; S5: with the
MMIO PL1 at 8 W the package held 8.07 W, each write changed only its bits, after the unload the register was bit-identical and 12.1 W were back; no
kernel warning, no new taint). Ctrl+C (idle phase) and SIGKILL (in the load, watchdog) undid everything. SIGKILL during the first write (module loaded): the watchdog unloaded the module within about 4 s and a later
run's own read of the register showed the firmware value; the leftover markers then locked S5 only, while S3 and S4 passed again. That completes the
check of 6.2 ("before testers get it"). **Not run on any device:** S4 and S5 on a copy the firmware has switched on (simulation only, 6.2), the
signing cases A, C and D, builds with clang, distributions other than Ubuntu's family. Wish of the maintainer (25.11. and 27.11.2026): a tester runs one script with `sudo`; it
does every check the device needs as automatically as possible, runs the tests cleanly, and delivers all the data we need. Markers as in
`blob-format.md`.

## 1. What a run does
1. **Before anything:** only one kit at a time; what an earlier run did not undo is undone first. Preflight (read only), the kernel check (3a), a
   notice for a checkout older than 14 days.
2. **The disclaimer and one question per risk class** (6.3), then the run goes on by itself.
3. **S1 to S5** (6). Without the load test a 120 s dry-run of the daemon runs instead of S2.
4. **Undo, summary, result file** (9), an optional free-text note, and with `gh` the offer to put the file into the device's issue.

Options: `--yes` (read and load questions only), `--no-load`, `--no-module`, `--no-issue`. Code: `tools/check.py` and `tools/kit/`.

**For kernel changes (wish of the maintainer, 27.11.2026):** every request of the kit's report to the controller is recorded with category, target,
command, instance (no payload: reads only) and the raw answer (`kit/evidence_ec.py`, result keys `ec_requests`, `kernel_nodes` with the platform hub's
ACPI id and whether the temperature and first fan node exist). The console says what the probe found and S1's summary repeats it; the probed sensors
are read every 5 s during S2, and the fan too where the kernel has no fan reading and the fan request answered. `check-eval.py` derives from this the
registry change a result gives evidence for: the group by the hub id (copy of the `v7.3-surface` registry), every other device that shares it, the
temperature node (all sensors the blob reads answered, plausible and named), the fan node (only if the speed followed the load) and `has_dptf`
(only for a group that lacks it, else the tester's kernel is old). It proposes; the patch, the check against the current kernel tree and the commit stay
manual under `CLAUDE.md` "Kernel changes". Tested with a simulated controller on the real report; **this path has not run on a device that lacks the node.**

The sensor probe (`tools/sensorprobe.py`) is part of the report since 0.2.1: where the blob reads SAM temperature sensors and the kernel has no sensor
node, it sends TMP `0x04`, then `0x0E` and `0x01` for the sensors in the mask, the requests of the kernel driver `surface_temp` [C]; without an answer
to `0x04` nothing else is sent. `--probe-sensors` forces it. Cross-checked on the Surface Book 3 against `surface_thermal` (`plan.md` D11) [M].

## 2. What the script never does
Install a package or a unit, enable a unit, change Secure Boot, lockdown or the MOK list, set a power limit higher than one the machine already has,
send anything to the internet by itself. It writes outside its own result files only: the undo and watchdog scripts and a lock under `/run`, the markers
under `/var/lib/surface-power-check/` (7), and during S5 the MMIO limit as 6.5 says.

## 3. Privacy and issue (implemented)
No serial numbers and no blob content. Only named fields are collected: from the kernel command line only options that matter here, from
`/proc/version` only the compiler's name, from the kernel log only MSR, machine-check and module lines. Before saving, host name, user names, full
names, home paths and MAC addresses are removed from every text. The tester sees the whole file before sharing it. One issue per device model in
`larsschellhas/linux-surface-power`, template `device-report.yml`.

## 3a. Kernel check (implemented 27.11.2026; tests on fakes, the "current" case also run on the SB3; wish of the maintainer)
Before anything runs, the kit judges the running kernel (`tools/kernelcheck.py`) and writes the verdict into the result.
- **What a release holds** [C: `patches/7.3/0007-surface-sam.patch` at the release tags of linux-surface/linux-surface, read 27.11.2026]: 7.3.4-2 has the
  suspend fix (kernel#197) and neither kernel#190 nor kernel#195; 7.3.5-1 (Debian, Arch, Fedora) is the first with both; the file has the same size in 7.3.6-1.
- **What an update adds per device** [C: registry of `v7.3-surface`], found through the ACPI id of the Surface platform hub: the DPTF battery data and adapter
  rating as kernel attributes on 15 ids (`MSHW0110, 0117, 0118, 0119, 0123, 0250, 0263, 0290, 0343, 0350, 0360, 0440, 0510, 0530, 0583`); temperature
  sensors and fan speeds on the Surface Book 3 (`MSHW0117`) only. No sensor node is added for the Pro 7+ or Pro 8 (D11).
- **The kit asks** "run with this kernel anyway?" (default no) when: a linux-surface kernel is older than 7.3.5-1; a newer one is installed but not
  booted (then it asks for a reboot); or the kernel's name is not a linux-surface release it knows **and** something expected on the device is missing, or
  the device is none of the above. If the answer is yes, the result records it. `--yes` goes on.
- **No question:** 7.3.5-1 or newer. A newer known release is mentioned in one line; an expected feature that is missing is reported as a finding.
- The advice says to keep the old kernel installed. On a Pro 9 Intel (`MSHW0343`) it also names the open report about the Type Cover after resume on 7.3.5
  and 7.3.6 (D12).
- The newest known release is a constant (7.3.6-1), raised by hand: the kit fetches nothing.

## 4. Findings that shaped the design
From the first four reports (issues #2–#5 of 25.–26.11.2026, #2–#4 evaluated first-pass) and from the code.

### 4.1 Module signing: what decides [C]
Kernel source `v7.3-surface-devel`, `kernel/module/signing.c` and `security/integrity/ima/ima_efi.c`, read 27.11.2026:
- **Not Secure Boot decides, but `sig_enforce` and lockdown.** With `CONFIG_IMA_ARCH_POLICY` and Secure Boot the kernel switches `sig_enforce` on at boot.
  The reference device's kernel (7.3.6-1-surface) has the option and reads `sig_enforce` Y with `CONFIG_MODULE_SIG_FORCE` off [M, 27.11.2026].
- With `sig_enforce` **off** and lockdown `none`, an unsigned module (and one signed with an unknown key) passes the signature check [C].
- With `sig_enforce` **on**, both are refused with `EKEYREJECTED`, "Key was rejected by service", and a log line "Loading of unsigned module is rejected"
  or "Loading of module with unavailable key is rejected" [C].

Consequences (0.3 follows the table of section 8):
| Report | State | What 0.2 did | Reading |
|---|---|---|---|
| Pro 7+ (#2), CachyOS | Secure Boot on, `sig_enforce` N, lockdown `none` | skipped: no DKMS key | the kit's condition (`Secure Boot or sig_enforce`) is wrong: by the source above no key is needed. That this build behaves like that source is [H] until the module loads there |
| Pro 8 (#4) | `sig_enforce` Y; a DKMS key file exists | `insmod` refused, "Key was rejected by service" | the kernel does not know the key [H: probably not enrolled; the kernel's log line was filtered out; `mokutil --test-key` answers it] |
| Pro 10 (#3), SB3 | `sig_enforce` Y, key enrolled | loaded | – |

`mokutil` 0.6.0 prints "… is already enrolled" and exits with status **1** [M, SB3, 27.11.2026]: the kit must read the text, not the status. The last word
is always `insmod` and the kernel's log line.

### 4.2 What 0.2 did not deliver [C]; all addressed in 0.3
- Only PL1/PL2 are shown. PL4, PL3, psys, their windows and the raw registers are missing, so the firmware state (procedure step 10) is incomplete.
- Nothing watches the limits during the run. The two Pro 10 runs of #3, with and without the tester's own PL4 service, agree in every reported value.
- The dry-run keeps `state`, `pl_path` and the invalid inputs. The input values and computed outputs are thrown away, so measurement against expectation
  (step 11) is impossible.
- With the kernel attributes, EC values are not printed, and the report calls the inputs "provided" because the attributes exist, not because a value was read.
- The zone comparison leaves out the time window and the energy counter.
- The module test runs next to thermald. With an MMIO zone, thermald 2.5.6 in adaptive mode writes `enabled=0` to the MSR copy at its start
  (`../knowledge/rapl-power-limits.md` 6) [C]; whether a running thermald reacts to a zone that appears is [H]. Test plan rule KM-13 stops it.
- The kernel's MSR and machine-check lines come from `dmesg`, whose older lines can be gone: two lines of the run of 11:47 on the SB3 were missing from the
  run of 11:52 in the same boot [M]; that the ring buffer had dropped them is [H]. The journal of the current boot is the better source (matters for F10).
- No load: no report says which limit is actually in force on the device.

## 5. Design: principles
1. **One command, the same for everyone:** `sudo ./scripts/surface-power-check`. No options needed.
2. **The tester delivers facts once; we compute everything that can be computed.** The blob is identified by its hash and is in our corpus, so slider states,
   postures and "what Windows would compute" come from an engine replay on our side. The tester does not click through power modes.
3. **All questions first, then it runs alone.** One screen with the stages, their duration and what each can do to the machine; one answer per risk class.
4. **The tester decides how far the check goes.** No release by the maintainer per tester or device: a stage that passed unlocks the next one (6.2).
   The repository only informs (what is known about this model) and can block a stage where a problem was reported.
5. **A stage runs only if the stages it depends on passed in this run, on this boot.** Nobody looks at the results in between, so every pass criterion is
   checked by the kit, and anything unexpected counts as not passed. A stage that cannot run says why and what would unlock it; independent stages still run.
6. **Everything is undone**, also after a crash of the kit (7).
7. **The result answers the questions without a follow-up**, and a program on our side reads it (9).

## 6. Stages
### 6.1 Stages and what they answer
| Stage | What it does | Risk class | Time (estimate [H]) | Answers |
|---|---|---|---|---|
| S0 preflight | checks the prerequisites; prints one command per missing one (headers, compiler), for the detected distribution; installs nothing | read | seconds | can the later stages run |
| S1 snapshot | identity, platform, software, all limits, EC values, sensor nodes or sensor probe, signing state | read | < 1 min | procedure steps 1, 3, 10; gates R1, R2, R3, R4; D4, D9–D11, F10 |
| S2 observe under load | idle 60 s, CPU load 150 s (one SHA-512 process per CPU), cool-down 60 s [numbers H, to be calibrated]; the daemon's dry-run runs at the same time; samples at 1 Hz | heats the machine | ~5 min | which limit is in force (DV-8), computed against measured (step 11), controllers and sensors under load (R7), daemon CPU and memory (B8), foreign writers |
| S3 module read only | `allow_write=0`: zone against register (PL1, PL2, enable, window), energy counter runs, unload bit-identical | loads a kernel module | 1–2 min | module table item 3, KM-A1, KM-A6 |
| S4 write permission, no write | `allow_write=1`, nothing written, unload bit-identical | module that may write | seconds | KM-A9 |
| S5 first write | lowers the MMIO PL1 below the limit in force, repeats the load, measures, unloads (restores), repeats the check | writes a power limit | ~5 min | module table item 5, KM-A8 |
| later | suspend cycle (L8, D5); control run of the daemon (R7) | writes | – | designed after S5 has run on two devices |

Device-specific extras (for example the fan 2 test of D7) can plug in as further stages with the same rules (none is built).

### 6.2 Unlock chain
**Decision of the maintainer, 27.11.2026:** going back and forth for every partial test is too slow; testers get the decision. S0–S2 are open on every SMF
device, S3–S5 on every Intel device with a profile. Within one run:

| Stage | Unlocked by | Also needs, checked live | Counts as passed when |
|---|---|---|---|
| S1 | S0 | – | – (what cannot be read is recorded) |
| S2 | S1 | AC, the tester's consent | the load ran to its end without the abort of 6.4 |
| S3 | S1 | Intel with a profile; no 00:04.0; module not loaded; headers and build tools; lockdown `none`; a signing case that can load (8); the watchdog runs; thermald is not running; the kit's own read of the register is plausible: MCHBAR equals the constant of the platform type, the unit register is sane, PL1 and PL2 between 3 and 400 W, no lock bit [numbers D]; consent | the register does not change at the load or while loaded; zone equals the register (PL1, PL2, PL1 enable, window); energy counter counts; after unload the register is bit-identical; no new bad taint bit (machine check, bad page, oops, warning, soft lockup) and no such line in the kernel log |
| S4 | S3 passed | no program that writes limits is running (6.4); consent | register unchanged at the load, while loaded and after the unload; no new kernel trouble |
| S5 | S4 and S2 passed | writers stopped; AC; S2 reached a plateau; the tester types the word the kit asks for | 6.5 |

- **Outcomes:** `passed`; `failed` (something unexpected happened to the machine or the register: recorded on the machine); `not-passed` (no verdict
  possible, or the write had no effect: nothing unexpected, nothing recorded); `skipped`. Only `passed` unlocks the next module stage. A module the
  kernel refuses to load is `skipped`: nothing changed.
- **The first stage that does not pass ends the chain:** no later module or write stage runs in this run, and the result says what differed.
- **A run that did not finish locks itself out.** Before each of S3, S4 and S5 the kit writes a marker to `/var/lib/surface-power-check/` and syncs it to
  disk; it removes it when the run has ended and the module is not loaded any more (Ctrl+C with a clean undo counts as ended). A leftover marker (freeze,
  power loss, killed kit, also if the watchdog cleaned up) means a run ended unclean. It leaves one marker per stage it had begun; **the last of them
  is where it ended:** that stage and the later ones do not run again on this machine, the earlier ones had finished and stay open (corrected after the
  SB3 run of 27.11.2026, which would have locked S3 and S4 for a kill during S5). Each marker names its run, so that several such runs are not mixed
  up; an unreadable marker counts for itself. The kit asks the tester to report it. A `failed` stage leaves a `failed-Sx` marker with the same effect. The markers of the running run do not lock it. This stands
  in for the maintainer looking after each step.
- **Stop list.** `kit-evidence.toml` in the repository can block a stage for a SKU, a platform type or everyone, with the reason that the kit then shows. It
  takes effect for a tester after `git pull`; the kit sends and fetches nothing, so it shows the age of the checkout and asks to pull first if it is older
  than 14 days [D]. A tester with an old checkout is not reached by a new block.
- **Before testers get it (once, not per tester):** the maintainer runs the whole chain through the kit on the Surface Book 3, including a killed kit
  (watchdog, 7) and a leftover marker, and the module's guard test KM-A5 exists. The same again whenever the module or a write stage changes.
  **Limit of this check:** the SB3's firmware leaves the MMIO copy disabled, so S4 and S5 on an enabled copy (Pro 7+, 8, 9, 10) run for the first time on
  a tester's machine. S4 exists for this: it writes nothing, and against a changed register there are two guards, the module's refusal of the core's
  clean-up writes and its restore at unload [C].
- **Not part of the chain:** the control run of the daemon. It sets the blob's limits, which can be higher than the firmware's, unlike every kit stage. Its
  gate is designed with the stage [proposal: S5 passed on the machine, the safety suite B7 passed for the profile, its own consent; no per-tester release].

### 6.3 What the tester sees
- **Console** (`tools/kit/term.py`, 27.11.2026): a heading per part, the machine's properties as aligned rows, text wrapped to the terminal (at most 96
  columns), one block per question with the answer typed in a short line of its own, during the load and countdown phases a live line (time, package
  power, CPU temperature, fan; without a terminal a line every 30 s), a result block per stage and a summary at the end. Colour only on a terminal and
  not with `NO_COLOR`; ASCII marks where the terminal cannot show others. The result file's format does not depend on it.
- **Per stage, what is known about this model,** from `kit-evidence.toml` (per SKU: passed reports with their issue numbers; `check-eval.py` proposes the
  entry, we commit it). For example:
  - "Passed on 3 machines of this model."
  - "Passed on 1 machine of this model (issue #3)."
  - "**You are one of the first.** This step has not passed on any Surface Pro 8 yet; we have no reliable data for it on your model." With what is known
    nearby (same processor family, or only other families).
  - "Blocked: <reason>."

  The profile's `tested` flag is shown too. It is not the source for the stages: it says whether the daemon may control by default and is `false` in every
  profile today, the reference device included, so it cannot tell the models apart.
- **The disclaimer,** before the first question; the run starts only after the tester types `I accept`. Text (English, as the kit prints it):

  > **Before you start.** This is experimental software. We have tried to make every test as safe as we can: each one checks its conditions first, the
  > module tests stop at the first unexpected result, the check undoes what it changed, and no step sets a power limit higher than the ones your
  > machine already has.
  > For the measurements it pauses services that manage power, such as thermald, so their throttling is off during that time; the processor's own
  > overheat protection stays active. We do not expect anything to go wrong. We cannot guarantee it, and many models have never run these tests.
  > What can happen: the machine may freeze or restart, and unsaved work is lost. Under load it gets warm and the fan may get loud. The later steps load a
  > kernel module that is not part of your kernel (the kernel stays marked "tainted" until the next boot), and the last one writes a power limit.
  > You run this on your own device at your own risk, and you are responsible for it. The software comes without any warranty (see the licence files).
  > Save your work, close other programs and connect the charger.

  The legal effect of this text is not checked [H]; the licences' own warranty disclaimers apply independently of it.
- **What is collected, before the question** (wish of the maintainer, 27.11.2026): the question to collect data is preceded by the list of what is
  collected (device, system, power software, power and heat, device state including whether a camera or an external screen is in use, power settings
  only as fingerprint, logs, test results), what is not, where the file goes (issues are public) and how it is read (`consent.COLLECTED`). A test
  fails for a part of the result's data that no entry of the list covers (top-level parts only: a new field inside one is not caught).
- **Consent** once per risk class (read; load; kernel module; writing), all asked before the first stage, each with the "what is known" line. Saying no
  skips that class and what depends on it. `--yes` answers the read and load questions only; it never accepts the disclaimer, the module or the writing.

### 6.4 S2 in detail
- **Conditions,** checked and recorded: on AC, battery level, load before the start, uptime, number of suspends since boot (so that "firmware values" is not
  claimed for a machine that something has already written).
- **Writers:** thermald and tuned write limits, so a measurement next to them says little. The kit (10, E2) stops `surface-powerd`, its dry-run unit,
  thermald, tuned, tuned-ppd, TLP and power-profiles-daemon for the load and module stages and starts afterwards exactly what ran before; tuned,
  tuned-ppd and power-profiles-daemon come back through D-Bus, so they are masked with `--runtime` meanwhile (a unit the tester had masked stays
  masked). Stopping does not undo what they wrote. The pause is part of the consent to the load or module tests. If a writer is still running, the
  result says "measured next to programs that write limits", and S4 and S5 do not run. Without a watchdog (7) nothing is paused and no module loaded.
- **Watch (all stages from S1 on):** every RAPL limit (all zones and constraints, MSR `0x601`, `0x610`, `0x615`, `0x65c`, the MMIO register) is read once a
  second and every change is logged with its time. This finds writers by what they do, bespoke services included. A change between two samples is missed.
- **Load:** one busy process per CPU, with no program the distribution would have to install. Which load reaches the limits is not known yet [H]: to be
  calibrated on the SB3. If the package power stays clearly below every enabled limit, the result says "inconclusive", not "no limit".
- **Samples:** package and platform energy counters (average power), package temperature, SAM sensors (hwmon, else the probe's requests every 5 s, only for
  sensors in the mask), fan rpm (the highest), mean CPU frequency, AC; the daemon's whole series (thinned only if the issue file gets too long) and its last status.
- **Plateau:** the mean package power of the last 60 s of the load; "steady" if the deviation is at most 1 W or 7 % [H]. A switched-on limit within
  1.5 W is named as holding it [H], else the verdict is "inconclusive".
- **Pass:** the load ran to its end. A missing steady plateau does not fail S2, but S5 then does not run.
- **Abort:** a skin sensor at 60 °C or the CPU package at 105 °C for 10 s (the daemon's emergency rules, `supervisor.md` 4), or a CPU temperature that
  cannot be read for 10 s, ends the load. Each load process ends as soon as the kit that started it is gone, and at its deadline at the latest, so a
  killed kit leaves none (tested with real processes and SIGKILL).
- **Not built:** the battery pass of the design ("unplug now").

### 6.5 S5 in detail
- **Preconditions:** the row of 6.2, all checked live; no lock bit; MSR `0x610` readable.
- **Only downwards, and so that it can be seen.** Target = (the lowest of both copies' PL1 and the plateau of S2) rounded down, minus 5 W, at least
  8 W; it must lie at least 3 W below both PL1 and below the plateau, else the stage is skipped [numbers D]. (Changed from the design's "lowest PL1
  minus 8 W": without a daemon the SB3 is held near 13 W by its platform limit, and a limit above that shows no effect.) Being below both copies, it
  allows no more power than before whichever copy is in force; "the lower enabled copy is in force" is still [H] as a general rule.
- **Enabled copy:** one write (`constraint_0_power_limit_uw` of the MMIO zone).
- **Disabled copy** (SB3): if the MMIO PL2 is above the MSR PL2, first the MMIO PL2 is lowered to it; then the PL1 target; then `enabled=1`. Skipped
  if the MSR PL2 cannot serve as that bound.
- **After every single write** the register is read: only the bits of that write may differ (PL1 field, PL2 field, or the four enable and clamp bits).
  Then PL1 must read the target and be on, and PL2 must not be above what was allowed.
- Load for 90 s, plateau of the last 30 s. `rmmod` (the module restores the register it found at load [C, M SB3]), register bit-identical, then a
  short load again: the package must draw clearly more than the target.
- **`passed`:** plateau within ±1.5 W of the target [H, to be set after the SB3 run], MSR copy unchanged, old behaviour back. **`failed`:** another bit
  changed, a write or the unload failed, the register was not restored, new kernel trouble. **`not-passed`:** no or too little effect, something else
  held the power lower, a writer showed up, the load was aborted.
- The kit blocks sleep (`systemd-inhibit` in a transient unit) while services are paused or the module may be loaded: after a resume the RAPL core
  writes limits back.

Raising a limit above the firmware value (what removes DV-8 on the Pro 10) is not part of the kit; it belongs to a control run.

## 7. Safety of the kit itself
- **One undo stack** (module, dry-run unit, sleep block, services, debug device module), each entry made before its change, run newest first on every
  exit and on SIGINT, SIGTERM, SIGHUP; a second signal does not cut the undo short. Every undo command succeeds when there is nothing left to undo.
- **Watchdog:** the pending undo commands are also kept as a shell script. A transient systemd unit polls the kit's process and runs the script when
  the process is gone (SIGKILL, crash) or after 40 minutes. A freeze or power loss cannot be undone this way: a reboot unloads the module and drops
  the runtime masks. If an undo script is left over at the next start, it is run before anything else.
- A bug in the kit during a run is caught: everything is undone and what was collected is saved.
- A second kit is refused (lock under `/run`).
- **Watch** (from S1 on): every RAPL limit (all powercap zones, MSR `0x601`, `0x610`, `0x615`, `0x65c`, the MMIO register) once a second; each change
  with time and stage (pause and restore have their own names) goes into the result. This finds writers by what they do. A change between two samples is missed.
- **KM-A5:** the module's write guard is tested in user space from its source (`tests/test_module_guard.py`).

## 8. Signing
The kit decides in this order and says which case applies:
| Case | Condition | The kit |
|---|---|---|
| A | `sig_enforce` off and lockdown `none` | loads the module unsigned; nothing to set up |
| B | enforced, a key pair of the distribution whose certificate is enrolled | signs with it |
| C | enforced, a key file exists but is not enrolled | skips S3–S5, names the case, points to the setup command |
| D | enforced, no key file | as C |
| E | lockdown `integrity` or `confidentiality` | no register comparison is possible (`/dev/mem` and MSR writes are blocked); S3–S5 skipped, S1 and S2 as far as they can read |

Key locations: Ubuntu `/var/lib/shim-signed/mok/` [M, SB3]; DKMS on other distributions `/var/lib/dkms/mok.{key,pub}`; akmods `/etc/pki/akmods/` [both H, not
checked on such a system].

**Without the module the kit still delivers most of the evidence** where lockdown is `none`: S1 reads MCHBAR from the host bridge, compares it with the
constant of the platform type (the module's own check), and reads the limit register, the MMIO unit register and the lock bits through `/dev/mem`. Missing
then are only "the zone equals the register" and "unload leaves the register unchanged".

**Setup for cases C and D (10 E1; K7, not built yet):** a separate command, `sudo ./scripts/surface-power-check --setup-signing`, never part of the normal run. The
normal run offers it to every tester whose module stages cannot run for lack of a usable key. It explains what enrolment means, uses the distribution's own tool or key where
there is one, runs `mokutil --import` in the tester's terminal (the tester types the one-time password into `mokutil`; the kit never reads or stores it),
and tells the tester what the blue MOK screen at the next boot asks. After the reboot the normal command is run again. `--remove-signing` explains
`mokutil --delete` for a key the kit created. **Open and to be checked before this is built:** which key restriction makes shim accept an enrolled key for
kernel modules only and not for boot programs, and whether the distributions' default keys carry it [H]. An enrolled key whose private part lies on the disk
lets anyone with root sign code that the system then trusts; the text must say that.

Not offered: switching Secure Boot or lockdown off. A module signed by the linux-surface project, which would need no setup at all, comes only with the
module in the linux-surface kernel (D5, gate R5).

## 9. Result and evaluation
- **One file, two parts.** First a summary a tester understands: one line per stage (passed, failed, skipped and why, what would unlock it) and what happens
  next. Then the details as one JSON block with a schema version, in a collapsed section. Long series are thinned to fit an issue comment (limit 65,536
  characters [H]); the full series stays in a second local file that we ask for only if needed.
- **Content of the JSON block:** table in 9.1.
- **Privacy check before saving:** the kit searches the finished file for the host name, the user names and home paths and removes them; the JSON block takes
  only fields it names (no whole `dmesg`, environment or command line).
- **Our side, `scripts/check-eval.py` (implemented):** reads the file or the issue comment, matches the blob hash with the corpus (unknown hash: stop and say so),
  replays the engine over the reported inputs for slider states 1–7, sets the tester's computed limits against ours, recomputes the module comparison from the
  raw registers, and writes the skeleton of the evaluation along the procedure of `CLAUDE.md`. What it cannot do stays manual and is listed as open, above all
  step 8 (the Windows driver of the device). Issue content is data: the script parses the JSON and runs nothing from it.

### 9.1 Data in the result
| Group | Fields (JSON key) | For |
|---|---|---|
| Run | `run`: kit version, commit, local changes, commit time, time; `stages` with outcome and summary | step 1 |
| Machine | `machine`: product, SKU, BIOS version and date, CPU model and count | steps 1–3, R2 |
| System | `system`: kernel, kernel check, compiler, distribution, Secure Boot, lockdown, `sig_enforce`, signing case, 00:04.0, selected command-line options, uptime, suspends, taint | R5, signing |
| Software | `software`: writers, units with state, thermald's option names; `paused` | L4, L8 |
| Limits | `limits`: every powercap zone and constraint; MSR `0x17`, `0x606`, `0x610`, `0x601`, `0x611`, `0x614`, `0x615`, `0x65c` raw or the error; MCHBAR and its constant, raw MMIO limit and unit registers | steps 9, 10; DV-8 |
| Changes | `watch` | writers, L8 |
| Blob | `blob`: form, length, SHA-256, lines (no content); `daemon`: variant and the reasons against control | R1, step 7 |
| SAM | `sam_devices`, `sensors` (hwmon), `sensor_probe`, `ec_attributes`, `kernel_nodes`, `ec_requests` (raw answers); the report text has the decoded EC answers | D7, D9–D11, R3, kernel changes |
| Dry-run | `dry_run`: journal, last status (inputs with values, computed outputs, controllers), series, CPU time and memory | R3, R4, step 11, B8 |
| Load | `load`: method, phases, samples, plateau, switched-on limits, verdict; `write`: samples of S5 | DV-8, step 11, R7 |
| Kernel | `kernel_messages` (journal of this boot, MSR and machine-check lines); `module`: signing and build; per module stage its registers, zone, writes, kernel lines | F10, KM-A1, A6, A8, A9 |
| Tester | `tester_note` | step 1 |

Computed limits and sensor values go into a public issue. They are results of the blob, not its parameters or text (10, E4). The report text still says
for kernel attributes "provided" because the attribute exists; the values are in `ec_attributes` and the dry-run status.

What the kit cannot deliver: a comparison with Windows on the same device (R7), the driver proof (step 8), and anything a stage that was skipped would show.

## 10. Decisions of the maintainer (27.11.2026)
| ID | Question | Decision |
|---|---|---|
| E1 | Signing: may the kit help enrol a key (8), changing decision 2 of 25.11.2026 ("no new MOK is enrolled")? | Yes, as proposed: case A needs no key; the setup is a separate command that the tester runs. Before it is built: the key restriction is checked |
| E2 | May the kit stop the writers for the measurement and start them again (6.4)? | Yes, asked once, with the watchdog of 7 |
| E3 | Who releases the module and write stages? | **Not the maintainer per device:** the unlock chain (6.2), with what is known about the model and a disclaimer (6.3). Replaces the proposed release table and release order |
| E4 | Computed limits and sensor values in the public result (9.1)? | Yes |

E1, E2 and E4 were accepted with the concept as a whole ("grundsätzlich klingt das gut"), E3 was changed. Decisions of 25.11.2026 that stay: module test
on every Intel device with a profile (the platform type per profile is in `check.PLATFORM`, a test keeps it complete), the debug device module loaded for
the run and unloaded again, one issue per model.

## 11. Build state
| Step | Content | State (27.11.2026) |
|---|---|---|
| K1 | S0/S1: full snapshot, signing cases, unsigned load in case A, JSON result, privacy check, disclaimer and consent | built; tests on fakes |
| K2 | `scripts/check-eval.py` | built; tests on fakes; replay equal to the SB3 daemon's limits [M] |
| K3 | undo stack with services, watchdog, watch | built; watchdog tested with a real shell and a killed process, not with systemd as root |
| K4 | S2: load, samples, abort | built; run on the SB3: steady plateau at the platform limit, peak 25 W package [M]; whether the load reaches the limits of other devices is [H] |
| K5 | S3, S4, unlock chain, markers, `kit-evidence.toml`, KM-A5 test | built; S3, S4 passed on the SB3 [M] |
| K6 | S5 | built; passed on the SB3 (disabled copy) [M]; the enabled-copy path only in simulation |
| – | **SB3 run of the whole chain** (6.2, with killed kit and leftover marker) | passed 27.11.2026, steps A–G [M] (`kit-sb3-test/TESTPLAN.md` in the working folder) |
| K7 | signing setup (8) | not built; first the key restriction has to be checked |

Testers get K1–K4 as soon as they pass on the SB3. They get S4 and S5 once the check of 6.2 ("before testers get it") has passed.
