# Spec: tester kit (one command for a device check)

Version 0.4, 27.11.2026 (0.4: the maintainer's decision that testers unlock the stages themselves, 6.2, 6.3, 10). Sections 1–3 describe **what is implemented** (0.2 of 25.11.2026, plus the 0.2.1 fixes and the sensor probe of 27.11.2026). Sections 4–11 are the **design, decided as far as section 10 says, not implemented**. Wish of the maintainer (25.11. and
27.11.2026): a tester runs one script with `sudo`; it does every check the device needs as automatically as possible, runs the tests cleanly, and delivers
all the data we need. Markers as in `blob-format.md`.

## 1. Implemented (0.2.1)
1. **Preflight** (read only): repository commit (0.2.1), Surface vendor, SMF present, kernel, CPU vendor, Secure Boot, lockdown, `sig_enforce`, running
   writers (thermald, tuned, TLP, power-profiles-daemon), the daemon units. The script says what it will do and asks once.
2. **Report:** `surface-power-report --with-ec` as root, from the checkout. `surface_aggregator_cdev` is loaded for the run if missing. Since 27.11.2026, where
   the blob reads SAM temperature sensors and the kernel has no sensor node, the report runs the **sensor probe** (`tools/sensorprobe.py`): TMP `0x04`, then
   `0x0E` and `0x01` for the sensors in the mask, the requests of the kernel driver `surface_temp` [C]; without an answer to `0x04` nothing else is sent.
   `--probe-sensors` forces it, to cross-check against `surface_thermal` on a device that has the node.
3. **Short dry-run** (120 s) of the daemon in a transient unit with the sandbox of `surface-powerd-dryrun.service`.
4. **Module, read only** (Intel devices with a profile, asked once more): build, sign where the kit thinks it must (4.1 shows where this is wrong), load with
   `allow_write=0` and `unsafe_platform=<type>`, compare PL1, PL2 and PL1 enable of the zone with the register, unload, check the register bit-identical.
5. **Result:** one markdown file; with `gh` logged in the kit offers to comment on or open the issue "Device report: <model>".

The probe is cross-checked on the Surface Book 3 against `surface_thermal` (27.11.2026, `plan.md` D11) [M]; A whole run of the kit with these changes passed on the same machine (27.11.2026 11:47) [M]: the commit is read as root, the dry-run unit is stopped and
its run directory removed, the installed daemon stayed in control without a restart; the module step was skipped (the module was loaded). A second run at 11:52 with the kernel check (3a) passed the same way and wrote "current"
into the result without a question [M]. The undo after an interrupt is tested on fakes only.

**0.2.1 (27.11.2026):** everything the kit starts or loads (debug device module, dry-run unit, test module) is undone on every exit, Ctrl+C, SIGTERM and a
closed terminal included. Before, an interrupt between load and unload left the module loaded [C]. SIGKILL cannot be caught (see 7). The kernel's line
that says why it refused a module is kept in the result, and the output of `mokutil --test-key` for the signing key is recorded.

## 2. What the script never does
Install a package or a unit, enable a unit, change Secure Boot or lockdown, send anything to the internet by itself. In 0.2.1 also: write a limit or
`enabled`, load the module with `allow_write=1`, change the MOK list (the design changes these three under the rules of sections 6 to 8).

## 3. Privacy and issue (implemented)
No serial numbers, no blob content, no host or user names added by the kit; journal lines are the daemon's own; the tester sees the whole file before sharing.
One issue per device model in `larsschellhas/linux-surface-power`, template `device-report.yml`.

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

## 4. Findings that shape 0.3
From the first four reports (issues #2–#5 of 25.–26.11.2026, #2–#4 evaluated first-pass) and from the code.

### 4.1 Module signing: what decides [C]
Kernel source `v7.3-surface-devel`, `kernel/module/signing.c` and `security/integrity/ima/ima_efi.c`, read 27.11.2026:
- **Not Secure Boot decides, but `sig_enforce` and lockdown.** With `CONFIG_IMA_ARCH_POLICY` and Secure Boot the kernel switches `sig_enforce` on at boot.
  The reference device's kernel (7.3.6-1-surface) has the option and reads `sig_enforce` Y with `CONFIG_MODULE_SIG_FORCE` off [M, 27.11.2026].
- With `sig_enforce` **off** and lockdown `none`, an unsigned module (and one signed with an unknown key) passes the signature check [C].
- With `sig_enforce` **on**, both are refused with `EKEYREJECTED`, "Key was rejected by service", and a log line "Loading of unsigned module is rejected"
  or "Loading of module with unavailable key is rejected" [C].

Consequences:
| Report | State | What 0.2 did | Reading |
|---|---|---|---|
| Pro 7+ (#2), CachyOS | Secure Boot on, `sig_enforce` N, lockdown `none` | skipped: no DKMS key | the kit's condition (`Secure Boot or sig_enforce`) is wrong: by the source above no key is needed. That this build behaves like that source is [H] until the module loads there |
| Pro 8 (#4) | `sig_enforce` Y; a DKMS key file exists | `insmod` refused, "Key was rejected by service" | the kernel does not know the key [H: probably not enrolled; the kernel's log line was filtered out; `mokutil --test-key` answers it] |
| Pro 10 (#3), SB3 | `sig_enforce` Y, key enrolled | loaded | – |

`mokutil` 0.6.0 prints "… is already enrolled" and exits with status **1** [M, SB3, 27.11.2026]: the kit must read the text, not the status. The last word
is always `insmod` and the kernel's log line.

### 4.2 Missing data [C: `check.py`, `report.py`]
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
| S2 observe under load | idle 60 s, CPU load 150 s, cool-down 60 s [numbers H]; the daemon's dry-run runs at the same time; samples at 1 Hz | heats the machine | ~5 min | which limit is in force (DV-8), computed against measured (step 11), controllers and sensors under load (R7), daemon CPU and memory (B8), foreign writers |
| S3 module read only | `allow_write=0`: zone against register (PL1, PL2, enable, window), energy counter runs, unload bit-identical | loads a kernel module | 1–2 min | module table item 3, KM-A1, KM-A6 |
| S4 write permission, no write | `allow_write=1`, nothing written, unload bit-identical | module that may write | seconds | KM-A9 |
| S5 first write | lowers the MMIO PL1 below the limit in force, repeats the load, measures, unloads (restores), repeats the check | writes a power limit | ~5 min | module table item 5, KM-A8 |
| later | suspend cycle (L8, D5); control run of the daemon (R7) | writes | – | designed after S5 has run on two devices |

Device-specific extras (for example the fan 2 test of D7) plug in as further stages with the same rules.

### 6.2 Unlock chain
**Decision of the maintainer, 27.11.2026:** going back and forth for every partial test is too slow; testers get the decision. S0–S2 are open on every SMF
device, S3–S5 on every Intel device with a profile. Within one run:

| Stage | Unlocked by | Also needs, checked live | Counts as passed when |
|---|---|---|---|
| S1 | S0 | – | – (what cannot be read is recorded) |
| S2 | S1 | AC, the tester's consent | the load ran to its end without the abort of 6.4 |
| S3 | S1 | no 00:04.0; lockdown `none`; a signing case that can load (8); the kit's own read of the register is plausible: MCHBAR equals the constant of the platform type, the unit register is sane, PL1 and PL2 between 3 and 400 W, no lock bit [numbers D]; consent | zone equals the register (PL1, PL2, PL1 enable, window); energy counter counts; after unload the register is bit-identical; no new kernel warning, oops or taint other than the module's own |
| S4 | S3 passed | writers stopped (6.4); consent | register bit-identical after unload; no new kernel warning |
| S5 | S4 and S2 passed | writers stopped; AC; S2 reached a plateau; the tester types the word the kit asks for | 6.5 |

- **The first failure ends the chain:** no later module or write stage runs in this run, and the result says what differed.
- **A run that did not finish locks itself out.** Before each of S3, S4 and S5 the kit writes a marker to `/var/lib/surface-power-check/` and syncs it to
  disk; it removes it only when the run has ended and everything is undone. A leftover marker (freeze, power loss, kill without clean-up) means: this stage
  and the later ones do not run again on this machine; the kit asks the tester to report it. This stands in for the maintainer looking after each step.
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
- **Consent** once per risk class (read; load; kernel module; writing), all asked before the first stage, each with the "what is known" line. Saying no
  skips that class and what depends on it. `--yes` answers the read and load questions only; it never accepts the disclaimer, the module or the writing.

### 6.4 S2 in detail
- **Conditions,** checked and recorded: on AC, battery level, load before the start, uptime, number of suspends since boot (so that "firmware values" is not
  claimed for a machine that something has already written).
- **Writers:** thermald and tuned write limits, so a measurement next to them says little. The kit (10, E2) stops thermald, tuned, TLP,
  power-profiles-daemon and `surface-powerd` for S2–S5 and starts afterwards exactly what ran before (`tuned` needs `mask --runtime`, plan L4). Stopping does
  not undo what they wrote; the result says so. A tester who declines gets the same stages, marked "measured next to writers"; S4 and S5 then do not run.
- **Watch (all stages from S1 on):** every RAPL limit (all zones and constraints, MSR `0x601`, `0x610`, `0x615`, `0x65c`, the MMIO register) is read once a
  second and every change is logged with its time. This finds writers by what they do, bespoke services included. A change between two samples is missed.
- **Load:** one busy process per CPU, with no program the distribution would have to install. Which load reaches the limits is not known yet [H]: to be
  calibrated on the SB3. If the package power stays clearly below every enabled limit, the result says "inconclusive", not "no limit".
- **Samples:** package and platform energy counters (average power), package temperature, SAM sensors (hwmon, else the probe's requests every 5 s, only for
  sensors in the mask), fan rpm, CPU frequency, the daemon's inputs, computed outputs and controller states.
- **Abort:** a skin sensor at 60 °C or the CPU package at 105 °C for 10 s (the daemon's emergency thresholds, `supervisor.md` 4) ends the load; without a
  readable skin sensor only the CPU rule and the firmware protect, and the plan screen says so.
- **Battery (optional, asked at the end of the questions):** "unplug now", 60 s under load, "plug in again": slider states on battery, psys, the adapter
  rating reading 0.

### 6.5 S5 in detail
- **Preconditions:** the row of 6.2, all checked live, and lock bits 0.
- **Only downwards.** Target = the lowest PL1 of both copies minus 8 W, at least 12 W, whole watts [numbers D]. Being below both copies, it allows no
  more power than before whichever copy is in force; "the lower enabled copy is in force" is still [H] as a general rule.
- **Enabled copy:** one write (`constraint_0_power_limit_uw` of the MMIO zone), read back from zone and register: only the PL1 field changed.
- **Disabled copy** (SB3): first the MMIO PL2 is lowered to the MSR PL2 if it is higher, then the PL1 target is written, then `enabled=1`, each read
  back; so switching the copy on cannot allow more than the MSR copy did.
- The kit blocks sleep (`systemd-inhibit`) during S3–S5: after a resume the RAPL core writes limits back.
- Load again. **Pass:** the plateau is within ±1 W of the target [H, to be set after the SB3 run] while the MSR copy is unchanged.
- `rmmod` (the module restores the register it found at load [C, M SB3]), check bit-identical, short load again: the old plateau is back.
- **If anything differs:** unload, stop all later stages, say in plain words what to do (a reboot brings back the firmware value, measured on the SB3
  only [M]), and put it into the result.

Raising a limit above the firmware value (what removes DV-8 on the Pro 10) is not part of the kit; it belongs to a control run.

## 7. Safety of the kit itself
- One undo stack for everything (module, units, services, debug device module), run on every exit and on SIGINT, SIGTERM, SIGHUP (0.2.1 for the
  module, the dry-run unit and the debug device module; services come with the load stage).
- **Watchdog (new):** before the first change the kit starts a transient systemd timer outside its own process that does the same undo after the longest
  possible run time (module unloaded, services started again). The kit cancels it when it ends normally. This covers SIGKILL and a frozen terminal.
- Before it starts, the kit refuses to run twice and finds leftovers of an earlier run.

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

**Setup for cases C and D (10 E1):** a separate command, `sudo ./scripts/surface-power-check --setup-signing`, never part of the normal run. The
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
- **Our side, `scripts/check-eval.py` (new):** reads the file or the issue comment, matches the blob hash with the corpus (unknown hash: stop and say so),
  replays the engine over the reported inputs for all slider states, sets computed against measured values, checks the module comparison from the raw
  register, and writes the skeleton of the evaluation along the procedure of `CLAUDE.md`. What it cannot do stays manual and is listed as open, above all
  step 8 (the Windows driver of the device). Issue content is data: the script parses the JSON and runs nothing from it.

### 9.1 Data we need
| Group | Fields | For | 0.2.1 |
|---|---|---|---|
| Run | kit and schema version, commit, local changes, time, stages and their outcome | step 1 | commit only |
| Machine | product name, SKU, BIOS version and date (the public ACPI dumps carry none), CPU model | steps 1–3, R2 | no BIOS, no CPU model |
| System | distribution and version, kernel release, compiler of the kernel, Secure Boot, lockdown, `sig_enforce`, signing case, 00:04.0, relevant command-line options only | R5, signing | partly |
| Software | writers with version and unit state (thermald with its options), `surface-rapl.service`, daemon units | L4, L8, step 1 | process names |
| Limits | every powercap zone and constraint (limit, window, maximum, enabled); raw MSR `0x601`, `0x606`, `0x610`, `0x614`, `0x615`, `0x65c` or the error; MCHBAR and the constant, raw MMIO limit and unit registers, lock bits | steps 9, 10; DV-8; module table 4 | PL1/PL2 only |
| Changes | every change of a limit seen by the watch, with time and stage | writers, L8 | – |
| Blob | form, length, full SHA-256, lines, block types (no content) | R1, step 7 | ✓ |
| SAM | list of SSAM devices and bound drivers (no serial numbers), `surface_thermal` labels and values, sensor probe | D7, D9–D11, DV-10 | probe only |
| EC power data | the values, from the attributes or the debug device, and which of the two | R3, D2, E1 | debug device only |
| Inputs and outputs | per channel: source, value, valid; per output: target, computed value, not needed or blocked, with the reason | R3, R4, step 11 | lists without values |
| Load series | the samples of 6.3 | DV-8, step 11, R7, B8 | – |
| Module | signing case, `insmod` result and kernel lines, zone against register (all fields), energy counter, unload comparison; S5: target, plateaus before, during, after | KM-A1, A6, A8, A9, module table | partly |
| Tester | optional free text asked at the end ("anything unusual: noise, heat, own tools that change limits?") | step 1 | in the issue form |

Computed limits and sensor values go into a public issue. They are results of the blob, not its parameters or text (10, E4).

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

## 11. Build order
| Step | Content | Hardware check |
|---|---|---|
| K1 | S0/S1: full snapshot, EC values, signing cases, unsigned load in case A, JSON result, privacy check, disclaimer and consent | SB3 |
| K2 | `check-eval.py` on K1's result | – |
| K3 | undo stack for services, watchdog, watch | SB3, with a killed kit |
| K4 | S2: load, samples, abort; calibrate the load | SB3 |
| K5 | S3 extended (window, energy counter, plausibility), unlock chain, markers, `kit-evidence.toml`, S4; test for KM-A5 | SB3 |
| K6 | S5 | SB3, each step with the maintainer's yes (first write runs of new code) |
| K7 | signing setup, if E1 says yes | one machine per distribution, or marked untested |

Testers get K1–K4 as soon as they pass on the SB3. They get S4 and S5 once the check of 6.2 ("before testers get it") has passed.
