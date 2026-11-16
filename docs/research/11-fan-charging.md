# Fan and charging outputs of the blobs (`B620798F`)

16.11.2026. `10-outputs.md` listed these outputs as "passed to the EC/MCU, commands not analysed". This file records what
the Windows receiver does with them and what a Linux path needs. Markers as in `README.md`. Nothing here was run on a device that
has these outputs: this machine is a Surface Book 3, whose blob has none [M]. All SAM facts are read from the Windows drivers.

## 1. Summary

`B620798F` carries **fan and thermal-limit settings for the EC**. It carries **no state-of-charge limit** (no "stop at 80 %"). The
charging channels (309–311) are battery temperature thresholds that reduce the charge rate, and only the newest ARM blobs have them.

| Channel | Windows name | Meaning | Unit, range | SAM command (TC, CID) | Linux path |
|---|---|---|---|---|---|
| 101–103 | – | minimum fan speed, fan 1–3 | rpm, 0–10000, step 100 | FAN `0x0b`, u16 | via cdev |
| 111–113 | – | upper limit of fan noise, fan 1–3 | SPL, 0–100 [H: dB(A)], float32 | FAN `0x0c`, float32 | via cdev |
| 121–123 | `O_FAN1_ACTIVE_SPL` | lower limit of fan noise ("active" SPL), fan 1–3 | SPL, float32 | FAN `0x12`, float32 | via cdev |
| 114–116 | `O_FAN1_MAX_SONES` | upper limit of fan loudness, fan 1–3 | sones, u8 | FAN `0x15`, u8 | via cdev |
| 124–126 | `O_FAN1_ACTIVE_SONES` | active fan loudness (floor), fan 1–3 | sones, u8 | FAN `0x14`, u8 | via cdev |
| 201–203 | `O_FAN1_PROFILE` | fan profile, fan 1–3 | 1–4 (Windows allows up to 30) | FAN `0x0e`, u8 | exists (kernel, `has_fan`) and via cdev |
| 301 | `O_FAN_CS_REQUEST` | fan state: 1 normal, 2 standby (sleep) | 1–2 | FAN `0x0f`, u8 | exists (kernel, suspend hooks) |
| 312 | `O_MCU_FAN_MAX_SONES` | loudness limit of the MCU fan | sones, u8 | FAN `0x16`, u8 | via cdev |
| 302 | `O_MCU_FAN_ON_TEMP` | temperature where the fan starts | 0.1 K, 3131–3451 | FAN `0x19`, u16 | via cdev |
| 304 | `O_MCU_FAN_RAMP_TEMP` | temperature where the fan ramps up | 0.1 K | FAN `0x1a`, u16 | via cdev |
| 305 | `O_MCU_FAN_MAX_TEMP` | temperature where the fan reaches its maximum | 0.1 K | FAN `0x1b`, u16 | via cdev |
| 306 | – | maximum fan SPL (no blob uses it) | float32 | FAN `0x1c`, float32 | via cdev |
| 307 | `O_MCU_FAN_CRITICAL_TEMP` | temperature of the fan's critical mode | 0.1 K | FAN `0x1d`, u16 | via cdev |
| 308 | `O_MCU_PROCHOT_TEMP` | PROCHOT assert temperature | 0.1 K | **TMP** `0x25`, u16 | via cdev |
| 309 | `O_NORMAL_CHARGE_TEMP` | battery temperature up to which charging is at the normal rate | 0.1 K | **TMP** `0x26`, u16 | via cdev |
| 310 | `O_SLOW_CHARGE_TEMP` | … slow rate | 0.1 K | **TMP** `0x27`, u16 | via cdev |
| 311 | `O_TRICKLE_CHARGE_TEMP` | … trickle rate | 0.1 K | **TMP** `0x28`, u16 | via cdev |

- **Verdicts:** "exists" = a kernel driver sends the same command today. "via cdev" = no kernel driver, but the SSAM debug device can
  send it as root. Nothing needs a kernel patch to work; a patch is only needed for a clean sysfs interface (6).
- **Confidence:** the command layout, category, command ID and payload are [C], read from the Windows drivers of three device families (Pro 10, Pro 11 Intel, Pro 12 arm64). What
  the EC does with the value is [H] everywhere except 201 and 301, whose numbers equal what Linux already sends. On the SB3, the
  profile and fan-state commands are accepted and change nothing [M], but the SB3 blob does not use them.
- **Names:** the Windows names exist only where the blob macros of the corpus define them.
- **Fans 2 and 3:** the instance number is the fan number. Only the Laptop Studio devices have outputs for a second fan.

## 2. Which device has which channel [C]

| Device family | Channels |
|---|---|
| Pro 10 (also 5G), Laptop 6 | 111, 121, 201, 301 |
| Laptop 5, Pro 9, Laptop Go 3 | 111, 201, 301 (Go 3: also 101) |
| Laptop 7 Intel and 5G, Pro 11 Intel | 114, 124, 201, 301 |
| Laptop 7 and Pro 11 Snapdragon | 101, 111, 201, 301 |
| Laptop Studio 2 | 101, 102, 111, 112, 201, 202, 301 |
| Laptop Studio 1, Laptop Go 2, Pro 8 | 101 (Studio 1: and 102) |
| Laptop 13" (arm64) | 114, 124, 201, 301, 302, 304, 305, 307–312 |
| Pro 12" (arm64) | 308–311 only |
| Surface Book 3, Pro 7/7+, Laptop 3/4, Go 1 | none |

The corpus lists the exact channels per device in `../devices/`. The Pro 10 family has no sones. The Laptop 7 Intel family has no SPL.

## 3. How Windows sends them [C]

- **Receiver:** the SMF client driver `SurfaceSmfClient.sys` has a fan module. It tells the SMF core which output channels it takes
  (the interface `B620798F`). The core calls it whenever the blob changes such a channel. The module turns the value into one SAM
  request and sends it to the Surface Serial Hub driver with a 100 ms timeout. No other Windows component is involved.
- **When:** the fan outputs sit in the 5 s evaluation instance, so a channel changes at most every 5 s. A request is sent
  1. on the first update after start or resume (the flags are set again when the device leaves D0), so everything is resent after
     every wake;
  2. later only if the value moved by at least the channel's dead band: 100 rpm for 101–103, 1 for all others.
- **Range:** a value outside the channel's blob range is clamped before sending.
- **Errors:** a failed request is logged. The last-sent value is not updated, so the next cycle tries again.
- **Read back:** none. The only read in the module is the system SPL (below). There is no query for the set values.
- **Input side:** the module also reads the *system SPL* (FAN `0x13`, instance 0, float32 answer) and feeds it into the blob as an
  input. No blob of the corpus reads that input.

### 3.1 SAM request layout [C]
Nine bytes for a write with a 2-byte payload; the same layout with a different length otherwise.

| Byte | Content |
|---|---|
| 0 | target category: 5 (FAN), or 3 (TMP) for 308–311 |
| 1 | target ID: 1 (SAM) |
| 2 | instance ID: the fan number (1–3); always 1 for 301, 302–312 |
| 3 | flags: 0 for writes (sequenced, no response); 1 for reads with a response |
| 4 | command ID |
| 5–6 | payload length, u16 little endian |
| 7… | payload, little endian |

This is the same data as the Linux `ssam_cdev_request` fields `target_category`, `target_id`, `instance_id`, `command_id`, `flags` and
payload. The SPL payload is the integer channel value converted to a 32-bit float. The temperature payload is the channel value in
0.1 K, so 3161 is 43.0 °C.

## 4. What the values are on Windows [C, engine replay of the blobs]
Replayed with `surface-power-replay` on the corpus blobs. Inputs not named were held at normal values.

- **Pro 10, 201 (fan profile):** 2, 3 or 4, from the power slider position (1–2 → 2, 3–4 → 3, 5 → 4). A hot skin sensor forces 4.
  Value 1 (battery saver) does not occur. The numbers are the same as the profile numbers `surface_platform_profile` sends (balanced 2,
  balanced-performance 3, performance 4).
- **Pro 10, 301 (fan state):** 2 while the system is in the sleep or legacy-sleep phase, otherwise 1.
- **Pro 10, 111 (noise limit):** 28 with the display off, 25 in the maintenance phase, otherwise 0.
- **Pro 10, 121 (noise floor):** 0 while the CPU is cool. From a CPU temperature of about 71 °C it rises from 18 to 25 at about 91 °C,
  with a hysteresis on the way down.
- **Laptop 7 Intel, 124:** 0 below a CPU temperature of about 75 °C, then 1 to 8 sones. 114 is 0 in normal operation.
- **Laptop 13 (arm64):** 312 follows the profile (8, 12, 15, 24 sones for profiles 1–4); the fan-on, ramp, maximum, critical and
  PROCHOT temperatures are fixed (39, 40, 47, 55 and 57 °C); the charge temperatures shift by a few kelvin with the slider.
- **0 means "no limit" or "no floor"** [H]: Windows sends 0 all the time in normal operation, so the EC must treat it as neutral.

## 5. The Linux side

### 5.1 What exists [C, kernel sources]
- **`surface_fan`** (hwmon): reads FAN `0x01`, instance 1, u16 rpm, as `fan1_input`. Read only.
- **`surface_platform_profile`:** writes the TMP profile (`0x03`). On nodes with the property `has_fan` it also sends FAN `0x0e` with
  1 (low-power), 2 (balanced), 3 (balanced-performance) or 4 (performance) at every profile change. In its suspend hooks it sends FAN
  `0x0f` with 2 (standby) before sleep and 1 (normal) after. These are exactly channels 201 and 301, with the Windows numbers.
- **`has_fan` is set** in the registry for the Laptop 5, 6, 7, Studio 2 and Pro 9 node groups. The Pro 10 probably uses the Pro 9
  group [H, not checked]. Devices whose group has no `has_fan` (e.g. SB3) get neither command.
- **`surface_battery`:** BAT `0x01`–`0x04` and `0x53` only (status, info, dynamic data, DPTF data, trip point). No charge threshold.
- **ACPI:** the Pro 10 has a fan device `FAN0` (`PNP0C0B`, "SAM FAN") that only depends on the SSH device and has no control methods.
  There is no ACPI path.

### 5.2 What is missing [C]
No driver sends FAN `0x0b`, `0x0c`, `0x12`, `0x14`–`0x16`, `0x19`–`0x1d`, or TMP `0x25`–`0x28`, and none reads FAN `0x13`.

### 5.3 User space through the debug device [C for the interface, M for FAN 0x0e/0x0f on the SB3]
- `/dev/surface/aggregator` (module `surface_aggregator_cdev`) is root only (mode `0600`). The request ioctl takes the category,
  target ID, command ID, instance ID, flags and a payload; flags 0 is a sequenced write without a response, flag 1 expects a response.
- The existing `CdevTransport` (`src/surface_power/adapters/inputs.py`) is read only and fixed to the battery category. For writes it
  needs the category, a payload buffer and `flags = 0`. A write request returns status 0 as soon as the EC acknowledges the frame
  on the transport. **Status 0 does not show that the EC knows the command** [M, SB3]: only the fan speed or a limit shows it.
- This is the same interim route the daemon already uses for EC inputs (NFR-11). It does not need a kernel patch.

### 5.4 Which firmware knows which command [C, from the Windows client versions]
- The SB3, Laptop Studio 1 and Pro 8 clients only know the minimum-rpm command. FAN `0x0e` and `0x0f` appear first in the Pro 9, Laptop 5
  and Go 3 clients, which matches the SB3 measurement (no effect) and the kernel's `has_fan` list.
- Sones commands appear with Studio 2, Pro 10 and Laptop 6. The maximum-SPL command, the fan temperatures and the TMP temperatures
  appear with Pro 10 5G, Pro 11 and Laptop 7 and in the arm64 clients.
- A device with an older EC firmware than its Windows driver may ignore a command and still return status 0 [H].

## 6. Options for a clean interface [H]
- **Interim, in the daemon:** send through the debug device as above, one adapter for the channels the device's blob writes.
- **Kernel:** a fan-limits interface does not map onto hwmon (`fan1_min` is an alarm threshold, not a floor). A custom attribute group
  next to `surface_fan` would be new ABI, so it should only be proposed after a measured effect.
- **Profile and standby (201, 301):** the existing driver is enough while the daemon follows the platform profile. A separate fan
  profile, independent of the TMP profile, still needs the debug device.

## 7. Risks
- **No read back, no snapshot.** The EC's current values cannot be read. "Restore" means sending the neutral value (3.1 and 8), and
  a reboot is the last resort [H: the EC returns to firmware defaults at boot; Windows resends everything at start].
- **A noise limit or a low fan floor can allow heat.** Windows only uses a low limit (25–28) with the display off or in standby. The
  EC's own safety limits are not known to hold for every combination [H].
- **301 = 2 (standby) while the system runs** may stop the fan. Only the kernel's suspend hook should send it.
- **302–311 change the EC's thermal and charging thresholds** (fan-on, critical, PROCHOT, charge rate). Raising them or sending a
  wrong unit can hurt the hardware or the battery. They are not part of the test below.
- **Wrong instance or command** on a different EC firmware is undefined. A frame with an unknown command can be acknowledged and ignored.
- Everything on this page is for a device you own, at your own risk, with AC power and a charged battery.

## 8. Proposed safe test procedure (for a user with a device that has these channels)
Goal: find out whether a channel does anything, and nothing else. Run as root, with no other thermal tool or daemon running.

### 8.1 Read-only probe (no writes)
1. Record the product name, kernel, `platform_profile` and the hwmon names. Check that `surface_fan` shows `fan1_input` and that a
   temperature source exists (`surface_thermal` or the CPU package sensor).
2. Send two read requests through the debug device: FAN `0x01`, instance 1 (rpm), and FAN `0x13`, instance 0 (system SPL, float32).
   Windows polls the second one all the time, so it is a normal request. An error status means the EC does not have it [H].
3. Log rpm and temperatures for 2 minutes at idle as a baseline.

### 8.2 Write test, one channel per run, in this order
| Order | Channel | Test value | Hold | Restore value |
|---|---|---|---|---|
| 1 | 201 | the value of the current platform profile, then one step up | 30 s each | the value of the platform profile |
| 2 | 121 (or 124) | 20 (or 3 sones) | 30 s | 0 |
| 3 | 111 (or 114) | 25 (or 8 sones), only with a light load | 30 s | 0 |
| 4 | 101 | 3000 rpm | 30 s | 0 |

- Do not test 301 by hand, 302–312, or any value above the range of the table in 1.
- A change shows as a step in `fan1_input` within about 10 s (the fan time constant is about 4 s on the SB3 [M]).
- Between tests wait for the baseline to come back.

### 8.3 Safety rules for the tool
- **Dry run first:** print the exact frames (category, command, instance, payload) without opening the device.
- **Refuse to start** if the battery is below 50 %, AC is missing, another writer holds the lock file, or any temperature is above the
  start limit (defaults: skin sensor 40 °C, CPU 70 °C; the user can only lower them).
- **Restore always:** the restore frames for every channel touched go out in a `finally` block and on `SIGINT`, `SIGTERM`, `SIGHUP`.
  Before the first write, fork a watchdog that holds a pipe to the main process. If the pipe closes (crash, `SIGKILL`) or 60 s pass without a
  heartbeat, the watchdog sends the restore frames. No write is held longer than 30 s, and a run is over after 10 minutes.
- **Abort on temperature:** every second read the temperatures. At skin ≥ 44 °C or CPU package ≥ 85 °C, or a sensor that stops
  answering, restore at once and exit with an error.
- **Verify the restore:** after restoring, log 60 s. If the rpm does not follow the temperature as in the baseline, say so and advise a
  reboot.
- **Fallback:** the last step is a reboot. The tool never raises a temperature threshold, never sends 301 = 2, and never writes a
  channel above its blob range.

```python
frames = []          # restore frames for every channel touched, filled before each write
def restore():
    for f in frames: send(f)      # neutral values; the profile frame uses the current platform profile
watchdog = start_watchdog(restore_frames=frames, deadline_s=60)
try:
    for step in plan:
        frames.append(step.restore); send(step.frame)   # restore frame first, so a crash mid-send is covered
        hold(step.seconds, abort_if=lambda: too_hot(), heartbeat=watchdog.beat)
finally:
    restore(); watchdog.stop()
```

## 9. Open points
- The effect on a real Pro 10 or Laptop 6: not measured. The probe in 8.1 and the test in 8.2 are the next step.
- Unit of the SPL values (dB(A)) and the exact role of 121 versus 111 [H]: the Windows driver names them "minimum" and "maximum" SPL.
- Whether FAN `0x13` and the read-only probe work on the SB3 and on older ECs [H]. Not run here: the debug device needs root.
- The instance ID of FAN `0x16` in the Pro 10 and Laptop 6 clients is never set (a Windows bug [C]); newer clients send instance 1.
- Charging: 309–311 only reduce the charge rate with battery temperature. A charge-level limit is not in this interface. Output
  `0B7B5350/70` ("battery mode") is not analysed.
