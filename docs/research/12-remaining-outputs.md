# The four remaining Pro 10 outputs

16.11.2026. After `11-fan-charging.md` the Pro 10 wrote 13 of 17 outputs. This note records what Windows does with the other four, the
Linux path chosen for each, and the checklist for a Pro 10 tester. Markers as in `README.md`. Nothing here ran on a device. Evidence:
the Pro 10 driver package (SMF client, SoC CPU client, thermal policy, SMF core, power tracker), the same drivers of the Pro 9,
Laptop Studio 2, Laptop 7 Snapdragon and Laptop 4 AMD packages, and the engine replay of the 41 blobs of the corpus.

| Output | Value in the Pro 10 blob | Windows receiver | Linux |
|---|---|---|---|
| `CF2F6076/61` | 1000, 8000, 28000 or 30000 (ms) | SoC CPU client: PL1 time window [C] | written: powercap `package-0/long_term` time window |
| `A79674B4/509` | constant 0 (0.1 K) | SMF client: virtual-temperature offset of sensor 9, SAM TMP `0x24` [C] | written: same SAM request through the debug device |
| `0B7B5350/70` | 1 without mains supply, else 0 | SoC CPU client: no hardware write, only a request flag to the SMF core [C] | not needed on Linux |
| `110DF2A4/3` | bit mask 0-7 | no client takes it; the power tracker only logs it [C] | not needed on Linux |

## 1. How the blob makes the values [C, engine and block parameters]
- **`CF2F6076/61`** is a lookup of the power slider: state 2 gives 8000, states 3-6 give 28000, state 7 gives 1000, all others (1, 8-10)
  give 30000. The unit is milliseconds [C: see 2.1]. The value does not depend on anything else (no temperatures, no EC data).
- **`A79674B4/509`** is the clamped sum of two lookup tables (posture, adapter rating) whose outputs are all 0: the value is 0 in every
  input state, also with every input invalid [C]. Other blobs have non-zero values: -2.0 K to +1.5 K (Pro 7+, Laptop 4 Intel, Laptop 5/6/7,
  Pro 11 Intel, Laptop Go 1), in 0.1 K.
- **`0B7B5350/70`** is a lookup of the adapter rating (`I_PSU_SIZE`): 1 when it is exactly 0, else 0 (an invalid rating gives 0).
- **`110DF2A4/3`** adds three flags: the outputs of three controllers (skin sensor 509, battery discharge rate, one more) divided by 15 W,
  each flag set when the result is 0-75 %, with weights 1, 2 and 4. So it is 0-7: "which controller is below 11.25 W".
  Other blobs reach 0-7 as well.

## 2. What the Windows receivers do

### 2.1 `CF2F6076/61`: PL1 time window [C code, H for the unit]
- The Intel SoC client has a table of target descriptors per channel: register (MMIO or MSR), bit field, shift. Channel 61 is a 7-bit
  field at bit 17 of the register that also holds PL1 (MMIO `0x59A0` on one table, `0x70A8` on another; channel 62 is the same field in
  MSR `0x610`). Bits 23:17 of the package power-limit register are the time window (5-bit exponent, 2-bit fraction) [SDM].
- Before writing, the client rounds the value **up** with a table of 28 thresholds, 1024 to 114688, four per octave. Code and
  threshold are the exponent and fraction of the field. With the register's default time unit 1/1024 s [H] the thresholds are 1.0 s to
  112 s: 1000 gives 1.0 s, 8000 gives 8.0 s, 28000 gives 28.0 s, 30000 gives 32.0 s. A value above the table gives code 0.
- The power tracker names the channel "average time" (`O_CPU_AVG_TIME`, `O_CPU_AVG_TIME2` for 62) [C, name tables of the Pro 9 and
  Laptop Studio 2 packages]. Same descriptors in the Laptop Studio 2 client.
- **Linux:** powercap `intel-rapl:0` (`package-0`) `constraint_0_time_window_us`, the `long_term` constraint that holds PL1. The value
  in ms becomes µs, is clamped to 1.0-112 s and rounded up to the same 28 steps. The kernel encodes the field. The old time window is in
  the snapshot (it was already, as the companion of the PL1 file) and is restored on exit. The read-back of a window can differ by
  up to ~14 % (4 steps per octave), so the foreign-change check allows 15 %.

### 2.2 `0B7B5350/70`: battery mode [C for "no write", H for the flag]
- In the SoC client, channel 70 has a descriptor of a special type (not MMIO, not MSR). The code path for it only sets a one-byte
  result flag when the value is 1 (value 1 in one plugin variant, 2 in the other) and writes nothing. The flag goes to the SMF core.
  In the thermal client's action-request code of the same package, small numbers like these name core actions (1 none, 2 refresh data
  to the core) [H: same enumeration]. The core ignores the answer of its write request [C].
- The battery driver does not take channel 70: it registers channels 300 and 400 (`O_BPM_DISABLE1/2`), which this blob does not have.
  No other kept Pro 10 driver names the channel. (The Pro 10 battery driver itself was not extracted; the Pro 9 one was checked.)
- **Linux:** nothing to write. The daemon reads every input itself each cycle, so a "refresh" request has no counterpart. Not needed,
  Intel only (the AMD and Qualcomm clients were not analysed).

### 2.3 `A79674B4/509`: virtual-temperature offset of sensor 9 [C]
- The SMF client takes the 12 channels 501-512 of this interface. For each updated one it sends a SAM request: target category 3
  (TMP), instance = sensor number, flags 0 (write), command `0x24`, payload 5 bytes: the sensor number as u8, then the value x 0.1
  as float32 (K) [C]. On EC (ACPI) platforms the same value goes to an ACPI method instead; the Pro 10 has
  SAM temperature sensors, so the SAM path applies [H]. Sensor 9 is the "virtual" skin sensor (`I_VTS1`, hwmon `temp9`).
- On the Pro 10 the request carries 0.0 K every time. Windows sends it once at start and after every resume.
- **Linux:** the same request through `adapters/fan.py` (spec outputs 9): same dead band, rate limit, clamp (hard bound +-5 K, the
  blob has no range for it), neutral frame = offset 0, recorded in the snapshot before the first send and sent at restore.
  Effect on the EC: not measured; 0 is what Windows sends [H: it equals the firmware default].

### 2.4 `110DF2A4/3`: O_MINPWR2 [C]
- The thermal-policy client registers channels 1 (`O_HOT`) and 2 (`O_CRT`) of this interface and no others (checked in the builds of
  Pro 10, Pro 9, Laptop Studio 2 and Laptop 7 Snapdragon). No other kept driver takes channel 3. The power tracker lists it (name
  `O_MINPWR2`) in its table of logged items; it does not act on it.
- **Linux:** not needed, all platforms.

## 3. Other devices
`scripts/device-docs.py` shows, per device page, which of the four channels the blob has:
- `CF2F6076/61` and `0B7B5350/70`: most Intel devices of 2020-2024 (Pro 7+, 8, 9, 10, Laptop 4 Intel/AMD, 5, 6, Go 1-3, Go 3/4,
  Studio 1/2); time window values are always from {1000, 8000, 28000, 30000}.
- `A79674B4/509`: Pro 7+ (-20, -10, 0), Laptop Go 1 (-20, 0), Laptop 4 Intel (0, 10), Laptop 5 (0, 10), Laptop 6, Laptop 7 and Pro 11
  Intel (0, 15), the others constant 0. Generic: every device with the SAM thermal sensors gets the same request.
- `110DF2A4/3`: 0-7 on newer devices, also arm64.
- The same rules apply: AMD and Snapdragon devices have no powercap, so `CF2F6076/61` and `70` stay without a path there.
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
   written, the PL1 window 28 s (slider 3-6) or 1/8/32 s, `A79674B4/509` sent once with 0.0, `0B7B5350/70` and `110DF2A4/3` "not needed".
   Stop with Ctrl-C or `systemctl stop`: the powercap values, the window, the cpufreq caps and the neutral fan and offset frames are restored
   (log line "firmware values restored"); if the process was killed, run `surface-power-reset`. Last resort: reboot.
6. **Report:** the report text, `status.json` from step 5, the journal, and whether the fan or temperatures behaved differently from the
   dry run. The effect of the TMP `0x24` request and of the fan frames on the EC is unknown: say if the status shows request failures.
- **Known gap:** `I_CPU_TEMP_AVG` is the unaveraged package temperature (DV-7) and `I_AMBIENT_AI` is not provided (reaches no output).

## 5. Open points
- Whether the register's time unit is 1/1024 s on the Pro 10 (default MSR `0x606` bits 19:16 = 10) [H]; Linux rounds with the real unit.
- Meaning of the SoC client's result flag for channel 70 beyond "request to the core" [H].
- Whether the EC treats offset 0.0 for sensor 9 like its power-on state [H], and whether `TMP 0x24` works on the Pro 10 firmware.
- The Pro 10 battery driver binary was not available; the Pro 9 one does not register channel 70.
- Provenance of the decompiles: `raw/tools/fw/decomp/p10c/` (Ghidra 12.2, `DecompEach.java`), local only.
