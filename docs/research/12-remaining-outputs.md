# The four remaining Pro 10 outputs

16.11.2026. After `11-fan-charging.md` the Pro 10 wrote 13 of 17 outputs. This note records what Windows does with the other four, the
Linux path chosen for each, and the checklist for a Pro 10 tester. Markers as in `README.md`. Nothing here ran on a device. Evidence:
the Pro 10 driver package (SMF client, SoC CPU client, thermal policy, SMF core, power tracker), the same drivers of the Pro 9,
Laptop Studio 2, Laptop 7 Snapdragon and Laptop 4 AMD packages, and the engine replay of the 41 blobs of the corpus.

| Output | Value in the Pro 10 blob | Windows receiver | Linux |
|---|---|---|---|
| `CF2F6076/61` | 1000, 8000, 28000 or 30000 (ms) | SoC CPU client: PL1 time window [C] | written: powercap `package-0/long_term` time window |
| `A79674B4/509` | constant 0 (0.1 K) | SMF client: virtual-temperature offset of sensor 9, SAM TMP `0x24` [C] | written: same SAM request through the debug device |
| `0B7B5350/70` | 1 while the adapter rating is 0, else 0 | SoC CPU client: no register write; value 1 sends one EC request (SAM TMP `0x1b`, ACPI-EC machines: method `DPHT` = de-assert PROCHOT [H for the link]) [C] | written: same SAM request once per episode (Intel, SAM machines); otherwise no path |
| `110DF2A4/3` | bit mask 0-7 | no client takes it; only the power tracker records it (telemetry) [C] | not needed on Linux |

## 1. How the blob makes the values [C, engine and block parameters]
- **`CF2F6076/61`** is a lookup of the power slider: state 2 gives 8000, states 3-6 give 28000, state 7 gives 1000, all others (1, 8-10)
  give 30000. The unit is milliseconds [C: see 2.1]. The value does not depend on anything else (no temperatures, no EC data).
- **`A79674B4/509`** is the clamped sum of two lookup tables (posture, adapter rating) whose outputs are all 0: the value is 0 in every
  input state, also with every input invalid [C]. Other blobs have non-zero values: -2.0 K to +1.5 K (Pro 7+, Laptop 4 Intel, Laptop 5/6/7,
  Pro 11 Intel, Laptop Go 1), in 0.1 K.
- **`0B7B5350/70`** is a lookup of the adapter rating (`I_PSU_SIZE`): 1 when it is exactly 0, else 0 (an invalid rating gives 0). So it
  follows the adapter: 1 on battery, 0 with a supply that reports a rating; no temperature, charge or standby input. The Snapdragon,
  Pro 12 and Laptop 13" blobs use the available input power (`I_IPL_TOP`) instead: 1 when it is 0 or above 200 W.
- **`110DF2A4/3`** adds three flags: the outputs of three controllers (skin sensor 509, battery discharge rate, one more) divided by 15 W,
  each flag set when the result is 0-75 %, with weights 1, 2 and 4. So it is 0-7: "which controller is below 11.25 W".
  Other blobs reach 0-7 as well.

## 2. What the Windows receivers do

### 2.1 `CF2F6076/61`: PL1 time window [C code and table, H for the unit]
- The Intel SoC client has a table of target descriptors per channel: register (MMIO or MSR), bit field, shift. Channel 61 is a 7-bit
  field at bit 17 of the register that also holds PL1 (MMIO `0x59A0` on one table, `0x70A8` on another; channel 62 is the same field in
  MSR `0x610`). Bits 23:17 of the package power-limit register are the time window (5-bit exponent, 2-bit fraction) [SDM].
- Before writing, the client rounds the value **up** with a table of 28 thresholds, 1024 to 114688, four per octave [C: table and
  lookup read in the Pro 10 client]. Each entry is a pair (threshold, 7-bit code) and the code equals Intel's encoding Y (bits 4:0) and Z
  (bits 6:5), time = 2^Y x (1 + Z/4) x time unit [SDM]; every code checked against its threshold. The client compares the
  millisecond value directly with the unit counts and never reads the time unit (MSR 0x606 bits 19:16), so it assumes 1 unit = 1 ms
  nominal. With the register's default unit 1/1024 s [H: not read on the Pro 10] the windows are 1.0 s to 112 s: 1000 gives 1.0 s,
  8000 gives 8.0 s, 28000 gives 28.0 s, 30000 gives 32.0 s. A value above the table gives code 0. Whether Windows writes the field on
  every change or only once is not shown by the client; the blob changes the value only with the slider state [H].
- The power tracker names the channel "average time" (`O_CPU_AVG_TIME`, `O_CPU_AVG_TIME2` for 62) [C, name tables of the Pro 9 and
  Laptop Studio 2 packages]. Same descriptors in the Laptop Studio 2 client.
- **Linux:** powercap `intel-rapl:0` (`package-0`) `constraint_0_time_window_us`, the `long_term` constraint that holds PL1. The value
  in ms is compared with the unit counts like the Windows client does (so 1001-1024 ms give 1.0 s), becomes µs, is clamped to 1.0-112 s
  and rounded up to the same 28 steps. The kernel then encodes the field itself and rounds **down** to its own 4 steps per octave
  with its time unit (976 µs for the default 1/1024 s) [C: `intel_rapl_common.c`]; the steps above are chosen so that this gives the
  same code as Windows. The old time window is in
  the snapshot (it was already, as the companion of the PL1 file) and is restored on exit. The read-back of a window can differ by
  up to ~14 % (4 steps per octave), so the foreign-change check allows 15 %.

### 2.2 `0B7B5350/70`: battery mode [C for the request, H for its meaning]
- The earlier reading "only a request flag to the SMF core" was wrong. The SoC client's write handler (all Intel plugin variants) sets a
  one-byte result when the value is exactly 1 (value 2 in the Qualcomm plugin). The client's own batch handler acts on that byte at
  once: it is not passed on to the SMF core, which only keeps the interface GUID in a list and has no logic for it [C].
- **Effect:** one EC request per delivery of the value 1, nothing for 0 (no "off" request) [C].
  - SAM machines: SAM request target category 3 (TMP), target ID 1, instance 0, flags 0, command `0x1b`, no payload (the Pro 8 client of
    2021 sends the same). Qualcomm plugin: command `0x1e` with a 4-byte payload `0x00001000`.
  - ACPI-EC machines (Go 3, Go 4; selected by a client setting): ACPI method `DPHT`. In the DSDT of these machines `DPHT` sends EC
    command `0x36`, which the table itself labels "de-assert PROCHOT" [C]. The SAM command sits at the same place of the same code
    with the same condition, so TMP `0x1b` is probably its SAM form [H]; it is not measured. The Pro 10 tables have no such method.
  - What the EC does with it (clear a PROCHOT that was set while the adapter was connected) is [H]; the EC asserts it again if the cause stays.
- Not an effect (searched in all Intel client builds): no write of the energy-performance bias (MSR `0x1B0`) or HWP request (`0x774`),
  no DTT or PPM mode, no change of the client's own power-limit tables. Its register table holds only MSR `0x601`, `0x610`, `0x615`,
  `0x65c` and four MMIO offsets (channels 51-59); channels 70 and 71 have no register [C].
- Other receivers: the battery client registers 250/300/350/400 (`O_BPM_DISABLE`), the GPU power controller 201, 202, 205, 208, 210, the VPU
  client channel 64, the power tracker only records `O_BATT_MODE` [C].
- **Linux:** `adapters/prochot.py` sends the same SAM request through the SSAM debug device once when the value turns 1 (also at the
  first cycle and after a resume), dry-run by default, retry after 5 s, log-only after three failures. Nothing is restored: the request
  releases a state and sets no level. No path (log-only, blocks readiness): ACPI-EC machines (the ACPI method cannot be called from user
  space), and AMD or Qualcomm CPUs (those clients are not analysed; the AMD packages of the corpus hold no CPU client at all).

### 2.3 `A79674B4/509`: virtual-temperature offset of sensor 9 [C for the frame, H for path and effect]
- The SMF client takes the 12 channels 501-512 of this interface. For each updated one it sends a SAM request: target category 3
  (TMP), instance = sensor number, flags 0 (write), command `0x24`, payload 5 bytes: the sensor number as u8, then the value x 0.1
  as float32 (K) [C]. On EC (ACPI) platforms the same value goes to an ACPI method instead; the Pro 10 has
  SAM temperature sensors, so the SAM path applies [H]. Sensor 9 is the "virtual" skin sensor (`I_VTS1`, hwmon `temp9`).
- On the Pro 10 the request carries 0.0 K every time. The module sends a channel when the core marks it updated; that this happens
  once at start and after a resume (as for the fan outputs) is [H]. Frame layout (category 3, target ID 1, instance = sensor number
  1-12 for channels 501-512, command `0x24`, length 5, sensor byte, float32 = value x 0.1) read from the SMF client of the Pro 10 [C].
  The client has a second path (an ACPI method) that it takes only for one platform type; the Pro 10 uses the group of the Pro 9 in the
  kernel registry with SAM temperature sensors [C], so the SAM path is the likely one [H]. The unit of the float (K) and the effect on the EC are [H].
- **Linux:** the same request through `adapters/fan.py` (spec outputs 9): same dead band, rate limit, clamp (hard bound +-5 K, the
  blob has no range for it), neutral frame = offset 0, recorded in the snapshot before the first send and sent at restore.
  Effect on the EC: not measured; 0 is what Windows sends [H: it equals the firmware default].

### 2.4 `110DF2A4/3`: O_MINPWR2 [C, search list below]
- **What drives it:** three flags from controller outputs (skin temperature, battery discharge rate, one more), so it changes with
  heat, discharge and slider, not with the adapter [C blob].
- **Searched:** the interface GUID (little-endian, ASCII, UTF-16, and its first four bytes alone) in all 127 distinct driver, service and
  DLL binaries of the corpus (215 files, 40 device packages, x64 and arm64). Hits: SMF core (list of interface GUIDs, no logic),
  power tracker (name table), `SurfaceService.exe` and `SurfaceThermalService.exe` (GUID lists without channel numbers), thermal policy. None in
  the SMF client, display client, sensor client, CPU client, battery, charger, hot-plug and integration drivers.
- **Thermal policy:** every build decoded registers channels 1 and 2 of this interface (newer builds: a loop 1..2; older builds such as
  Go 3, Laptop Go 2: the first two outputs not matched to a zone name) and one more on `9BBB8910`; none takes channel 3 [C]. (18 distinct thermal-policy builds of the corpus; the blobs that have channel 3 all belong to the newer builds.)
  The zone policy structure it fills has a hibernate flag (`O_HOT`), a critical flag (`O_CRT`) and a passive limit; its other fields
  (standby, activation reasons, active level) are never written, so there is no field channel 3 could map to [C].
- **Pro 10 ACPI:** `TPOL` has only `_STA` and `OPTS`; no method takes a battery-mode or minimum-power argument [C].
- **Power tracker:** lists it as `O_MINPWR2` among the items it records into its ETW events; no hardware access [C].
- **Linux:** not needed: the value is recorded by Windows telemetry only. A reader of those ETW events outside the corpus cannot
  steer hardware.

## 3. Other devices
`scripts/device-docs.py` shows, per device page, which of the four channels the blob has:
- `CF2F6076/61` and `0B7B5350/70`: most Intel devices of 2020-2024 (Pro 7+, 8, 9, 10, Laptop 4 Intel/AMD, 5, 6, Go 1-3, Go 3/4,
  Studio 1/2); time window values are always from {1000, 8000, 28000, 30000}.
- `A79674B4/509`: Pro 7+ (-20, -10, 0), Laptop Go 1 (-20, 0), Laptop 4 Intel (0, 10), Laptop 5 (0, 10), Laptop 6, Laptop 7 and Pro 11
  Intel (0, 15), the others constant 0. Generic: every device with the SAM thermal sensors gets the same request.
- `110DF2A4/3`: 0-7 on newer devices, also arm64.
- The same rules apply: AMD and Snapdragon devices have no powercap, so `CF2F6076/61` stays without a path there; `70` has none either
  (Intel SAM machines only; Go 3 and Go 4 are ACPI-EC machines).
- With these rules 9 more Intel devices cover every output (device pages); all stay dry-run by default (no profile, or `tested = false`).

## 4. Pro 10 tester checklist (technosf)
The profile `surface-pro-10.toml` has `tested = false`: the daemon never controls unless asked. Everything runs as root only where
stated. No other power tool (thermald, tuned, TLP) may run. AC power, battery above 50 %, nothing else heavy running.
1. **Facts:** `surface-power-report` (shows blob, inputs, and per output "writable (...)" / "not needed on Linux: ..."; `ready for control`
   must be `yes`). Send its text with the results.
2. **Probe, nothing written:** `sudo python3 -I scripts/fan-test.py OUT --probe` (reads the fan rpm and system SPL).
3. **Dry run of the daemon:** `surface-powerd --dry-run` for 10 minutes; look at the status file `/run/surface-power/status.json`: every
   output has a target, `fan` and `outputs` show `would send ...`, nothing written. Check `journalctl` for warnings.
4. **Single-channel tests**, each with its own script, one at a time (every script restores and logs; run `--dry-run` first):
   `scripts/fan-test.py OUT --channel 201` then `121`; `scripts/throttle-test.py` (the `/62` cap); `scripts/pl34-test.py` (PL4 and PL3).
5. **Daemon in control mode,** short and watched: `sudo surface-powerd --control` for 5 minutes at idle, then with a CPU load. Watch
   `sensors` (skin, CPU), `fan1_input`, `cat /sys/class/powercap/intel-rapl:0/constraint_0_*`. Expected in the status file: PL1/PL2/psys
   written, the PL1 window 28 s (slider 3-6) or 1/8/32 s, `A79674B4/509` sent once with 0.0, `0B7B5350/70` "SAM TMP 0x1b (cdev)" (one request while on battery, check the journal line), `110DF2A4/3` "not needed".
   Stop with Ctrl-C or `systemctl stop`: the powercap values, the window, the cpufreq caps and the neutral fan and offset frames are restored
   (log line "firmware values restored"); if the process was killed, run `surface-power-reset`. Last resort: reboot.
6. **Report:** the report text, `status.json` from step 5, the journal, and whether the fan or temperatures behaved differently from the
   dry run. The effect of the TMP `0x24` request and of the fan frames on the EC is unknown: say if the status shows request failures.
- **Known gap:** `I_CPU_TEMP_AVG` is the package temperature as the Windows client reads it, one sample without averaging [C] (DV-7) and `I_AMBIENT_AI` is not provided (reaches no output in any blob that reads it, `09-newer-blobs.md` section 6).

## 5. Open points
- Whether the register's time unit is 1/1024 s on the Pro 10 (default MSR `0x606` bits 19:16 = 10) [H]; Linux rounds with the real unit.
- Whether TMP `0x1b` really is the EC's de-assert-PROCHOT command and what the EC does with it on battery [H]; also whether the core delivers the value on change only or with every batch (Linux sends once per episode).
- Whether the EC treats offset 0.0 for sensor 9 like its power-on state [H], and whether `TMP 0x24` works on the Pro 10 firmware.
- The Pro 10 battery driver binary was not available; the Pro 9 and Laptop 7 ones do not register channel 70.
- Provenance of the decompiles: `raw/tools/fw/decomp/p10c/` (Ghidra 12.2, `DecompEach.java`), local only.
