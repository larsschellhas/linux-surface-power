# Spec: input adapters

Version 0.6, 26.11.2026 (0.6: a detached base battery is not asked, B14; posture 2 measured; 0.3: base sensors absent and looked up again, B13; 0.4: section 5 checked in the SB3 display client, G1; 0.5: measured under Windows, no nits estimate). Implements FR-I1 – FR-I8, NFR-11. Tests: AC-I1 – AC-I3. Markers as in `blob-format.md`.

## 1. Interface
- **Adapter role:** each adapter owns one or more device input channels (`GUID/channel`). On `read(now_ms)` it returns `{channel: value | None}` in blob units. `None` means invalid.
- **Reader:** the daemon calls all adapters once per second and passes the result to `Engine.set_inputs()`. It never passes zero for "missing" (FR-I4).
- **Unknown channels:** a channel that the blob reads and no adapter provides is reported once at start and stays invalid (FR-I8).
- **Overrides:** a device profile (`supervisor.md` 6) may override a mapping. It may not invent values.

## 2. Channel mapping

| Interface | Channel | Meaning | Linux source | Conversion |
|---|---|---|---|---|
| `2E8B14C2` temperature | 501 – 599 | SAM sensor `iid = ch − 500` | hwmon `surface_thermal`, `temp<iid>_input` (on the SB3 `temp9` is labelled `I_VTS1` = channel 509 [M]) | `0.1 K = 2731 + m°C / 100` |
| | 601 – 699 | KIP (base) sensor `iid = ch − 600` | hwmon `surface_thermal` whose SSAM device is `01:03:02:00:02` (target KIP, below the base hub). The nodes are in linux-surface since kernel#190 (merged 17.11.2026; built in from 7.3.6-1-surface [M]). They exist only while the base is attached; without the device the channel is **absent** (`supervisor.md` 4.1), and the device is looked up again (at most every 5 s, name `surface_thermal`, SSAM category 03, target 02), because its hwmon index can change after a re-attach [M] | 0.1 K |
| | 101, 102 | GPU, CPU temperature | 102: hwmon `coretemp` "Package id 0"; 101: NVML on the Laptop Studio (section 8.10) | as above |
| `2DD6960F` system state | 1 | slider state 1 – 10 | `platform_profile` × AC/battery (section 3) | integer |
| `28B7BD85` power tracker | 1 / 101 | charge level of battery 1 / 2 in % | `power_supply` `BAT1` / `BAT2` `capacity` [M: BAT1 is the display part at 8.0 V, BAT2 the base at 12.6 V] | % |
| | 2 / 102 | battery peak power `I_BAT_PEAK` | SSAM `0x53` @1, SAM / KIP | mW |
| | 3 / 103 | battery sustained power `I_BAT_MAX` | SSAM `0x53` @5 | mW |
| | 4 / 104 | `I_BAT_RFHE` (mΩ) | SSAM `0x53` @9 | mΩ |
| | 5 / 105 | `I_BAT_VNL` (mV) | SSAM `0x53` @11 | mV |
| | 6 / 106 | rest of platform `I_ROP_DYN` | SSAM `0x53` @17 [H] | mW |
| | 7 | PSU size | SSAM DPTF `0x0f` ARTG | mW |
| | 8 / 108 | available input power `I_IPL_TOP` / `I_IPL_BASE` | SSAM `0x53` @13, SAM / KIP | mW |
| | 10 / 110 | charge/discharge rate of battery 1 / 2 `I_BCDR` | `power_supply` `power_now` when `status` is "Discharging", else 0 [H for sign and for 0 while charging] | µW → mW |
| | 12 | battery current `I_BCDR1_I` (newer blobs) | `BAT1` `current_now`, else `power_now` / `voltage_now`; sign from `status` (section 8.3) | mA |
| | 13 / 16 | discharge-rate EWMA level / max charge reached | SSAM battery `0x1A` / `0x69` (section 8.4) | byte |
| `8060B17F` posture | 1 | posture 1 – 5 | `surface_dtx` mode + lid (section 4) | integer |
| `5D0EE597` lid | 1 | lid state | lid switch (section 4) | 1 = open, 2 = closed [H] |
| `1FB53D4C` power/state | 41 | display brightness in millinits (SB3 era) | not provided by default (section 5) | |
| | 42 | display brightness in % (newer blobs) | backlight `brightness` / `max_brightness` (section 8.1) | % |
| | 3 | dGPU power (Laptop Studio) | NVML, section 8.10 | mW |
| | 31 | on battery (Laptop Studio dGPU blobs) | not `online` of the `Mains` supply (section 8.7) | 0 / 1 |
| `8060B17F` platform events | 2 / 3 / 9 | external display / camera in use / Surface Docks | DRM, video4linux, USB (section 8.2) | 0 / 1 / count |
| `DEF1AB69` Modern Standby | 100 | standby phase `I_MS_NOTIFY` | constant 4 (section 8.5) | 1 – 5 |
| `049E36F2` power tracker | 9 | USB source power | Type-C ports in source role (section 8.6) | mW |
| `87008365` client-computed | 113 / 5 | proximity sensor state / Xbox controller | 0 / input devices (sections 8.8, 8.9) | |
| `2E8B14C2` temperature | 101 | dGPU temperature (Laptop Studio) | NVML, section 8.10 | 0.1 K |

The placeholder channel `99999999-…/1` (`blob-format.md` 5.2b) is never provided and is not reported as missing.

Readings outside the input-line range (`blob-format.md` 5.1) are invalid (FR-I2) [H: the Windows client reports a range error and drops the value].

**Availability of the SAM temperature sensors (`2E8B14C2/5xx`) [C, registry read 23.11.2026].** The hwmon source `surface_thermal` exists only
where the kernel registry has the TMP sensor node `01:03:01:00:02`: Book 3, Pro 9/10/11 and Pro 12 Intel, Laptop 5, Laptop 6, Laptop Studio 2. It is
missing for the Pro 7+, Pro 8, Laptop 3/4 AMD (`MSHW0110`), Laptop 4 Intel (`MSHW0250`), Laptop 7, Laptop Go 1/2/3 and Laptop Studio 1. There the inputs
are invalid (DV-10) until nodes are added (`plan.md` D9, D10). A tester's report on a Laptop 4 Intel 15" lists 505 and 509 as "not available"
and shows that the EC answers the sensor requests through the debug device (mask `0x07f6`, `I_VTS1` = sensor 9).

## 3. Slider state (FR-I3, OD-4)
| `platform_profile` | AC | battery |
|---|---|---|
| `low-power` | 1 (Recommended) | 7 (energy saver) |
| `balanced` | 1 (Recommended) | 2 (Recommended) |
| `balanced-performance` | 3 (Better) | 4 (Better) |
| `performance` | 5 (Best) | 6 (Best) |
| missing or unknown | invalid | invalid |

- **AC detection:** `power_supply` of type `Mains` (on the SB3 `ADP1`), `online == 1`. If no such supply exists, `0x0d` PSRC is used.
- **Low-power on AC:** Windows has no energy saver on AC. `low-power` on AC therefore maps to state 1, the Windows "Recommended" setting.
- **States 8 – 10:** game mode, mixed reality and a service flag have no Linux counterpart and are never produced.
- **Source:** `/sys/firmware/acpi/platform_profile` (the legacy attribute; on Fedora/Bazzite it is the only one, Surface Pro 9 field report). Where it is missing, the first `/sys/class/platform-profile/*/profile`, a `surface` device first.
- **Change detection:** a profile change is seen at the next 1 Hz read and causes an asynchronous run (`engine.md` 3.3, AC-I3).

## 4. Posture and lid (FR-I6)
- **Mode:** `surface_dtx` (`/dev/surface/dtx`, ioctl `SDTX_IOCTL_GET_DEVICE_MODE`) gives 0 = tablet, 1 = laptop, 2 = studio.
- **Lid:** from `/proc/acpi/button/lid/*/state`.
- **Mapping** (Windows hot-plug driver [C]; for value 3 the Windows side is [H], the Linux side measured [M]):

| Device mode | Lid open | Lid closed |
|---|---|---|
| laptop | 1 | 2 |
| studio | 5 | 4 |
| tablet or no base | 3 | 3 |
| dtx not available | 1 [D] | 2 [D] |

**Measured on the SB3 15" (15.11.2026, lid open):** the device mode follows the base as expected. Attached gives mode 1 (posture 1),
detached gives mode 0 with base state 0 (posture 3), and the display attached the other way round gives mode 2 (posture 5).
- During detaching, mode 0 appears up to 2 s before the base state changes. During attaching, mode 1 can show for under a second before mode 2.
- The Linux side of the mapping is confirmed. Whether Windows also reports 3 for a detached display stays [H]. Closing the lid in studio mode changed nothing: `/proc/acpi/button/lid` stayed open, so posture 4 is not seen on Linux this way. Laptop with the lid closed (2): measured 26.11.2026 (posture 2 from 11:29:50 to 11:31:48, daemon time series, no suspend): the computed PL1 went from 30 to 15 W and the dGPU limit from 50 to 25 W [M].

The SB3 15" blob reads only posture 1, through the PL1 and dGPU posture tables. Laptop, the normal case, gives 30 W, which does not limit.

## 5. Display brightness (FR-I7)
- **Use:** channel 41 enters PL4 only, through the estimated display power. Linux has no nits value.
- **Default:** the channel stays invalid. The engine then uses the block default (3 W display power), exactly like Windows without a brightness value.
- **Windows source [C, `SurfaceSmfDisplayClient.sys` 8.30.137.0, same file as in the SB3 package, 25.11.2026]:** a timer with the
  period of the blob (1000 ms) sends IOCTL `0x230c08` to the monitor device interface and takes the u32 at byte 4 of the 12-byte
  answer, without looking at the version field. A value is sent only when it changed and lies in the blob range (0 … 600000).
- **Measured under Windows [M, SB3 15", 25.11.2026, one 42 s trace of the driver's own messages; the running driver has the same
  build ID as the analysed file]:** the timer runs every 1000 ms and every time takes the branch "monitor target not open", so the
  request is **not sent** (40 of 40 ticks, brightness stepped from 0 to 100 %). The power-setting callback sends nothing either, because
  the SB3 blob does not read channel 42. A user process that sends the same request gets "access denied", which says nothing about the
  driver.
- **Consequence:** the timer is the only path that sends a value for channel 41, so in the measured state the core gets none and uses the
  block default (3 W), as this daemon does. Open [H]: whether the target was never open since the driver started (the trace starts
  later); that the core does not read the status message the client sends at start (channel online, 0) as a value (the core's own
  diagnostic texts tell status from data, the receiving code was not traced); why the target is not open. If the core did read that 0,
  Windows would subtract 5 mW and Linux's PL4 would be 3 W lower than Windows.
- **The block default (all SMF blobs with a brightness input in the local corpus, 26.11.2026):** in the blobs that take percent
  (channel 42, 24 files) the default is above the curve's value at 100 % in 19 files, equal to it within 1 mW in 3 (3100 mW) and
  below it in 2 (Surface Pro 7+) [C, parameters]. That the default is meant as the largest display power fits most of them [H]. On the
  SB3 3000 mW is the curve's value at 304 000 millinits (15") and 362 000 (13"); the panels' real power is not measured [H].
- **No nits estimate (decided 26.11.2026, plan G1):** the channel stays invalid. A live estimate would differ from the constant 3 W
  that Windows uses if the core got no value. If the core read the start status as 0 (5 mW), an estimate below 3 W would be closer to
  Windows than the default and one above 3 W farther from it. Neither the panel's luminance nor its power is measured. The profile key
  `inputs.panel_max_nits` is read by the loader and used by no adapter; it stays unused on the SB3.

## 6. EC requests for platform power data (FR-I4, NFR-11)
The same payload also arrives as an EC event on some devices (surface-aggregator-module#29, values there fit the field names below).
- **Preferred source: kernel attributes.**
  - `power_supply/BAT<n>/dptf/{max_platform_power_mw, max_steady_state_power_mw, high_freq_impedance_mohm, no_load_voltage_mv, available_input_power_mw, rest_of_platform_power_mw}` come from `surface_battery`, which applies the EC event.
  - `power_supply/ADP1/dptf/adapter_rating_mw` comes from `surface_charger`. It gives ENODATA while no rating is known. Each read is an EC request, so the daemon reads it when the power source changes (AC online, or a battery's available input power moves) and otherwise every 60 s.
  - Older kernels (installed systems) name the groups `dptf_battery` and `dptf_power`; the daemon tries `dptf` first, then the old name. Attribute names are the same.
  - The names follow the DPTF participant ABI (`sysfs-platform-dptf`).
  - Prototype patches tested on the SB3 15" (15.11.2026 [M]). They are not upstream yet.
  - With them, these channels need neither root nor the debug device. The values match the debug-device readings exactly.
- **Fallback:** the requests below through the debug device, per battery, only where the kernel attributes are missing.
- **Requests:**
  - **`0x53` "TpUpdate"**: target category 0x02 (BAT), command 0x53, instance 1, with response; sent to SAM and to KIP. The 21-byte answer:
    - @0 u8 version;
    - @1 u32 peak power (mW);
    - @5 u32 sustained power (mW);
    - @9 u16 resistance (mΩ);
    - @11 u16 open-circuit voltage (mV);
    - @13 u32 available input power (mW);
    - @17 u32 rest of platform (mW).

    The field order is [M] for the values and [H] for the meaning of fields other than peak power.
  - **DPTF reads** (TC 0x02, instance 1): `0x0b` PMAX, `0x0c` PSOC, `0x0d` PSRC, `0x0f` ARTG. Each answers a u32; `0x80000000` means "not present".
- **Timing:**
  - at start;
  - on every change of the AC state (`power_supply` uevent, or the 1 Hz read seeing a change);
  - after resume;
  - otherwise every 60 s [D].
- **Validity:** a failed request makes its channels invalid. They are never 0. A 0 in an answer is a value, with one exception below.
- **Only what the blob reads:** a command the EC does not know times out (kernel#195 review). The daemon sends `0x53` per battery only if the blob reads a channel of that battery, `0x0f` only for `28B7BD85/7`, `0x1A` only for `/13` and `0x69` only for `/16`. The Pro 9 blob reads `/13` but not `/16`, so it never gets `0x69`; it has no base battery (KIP), so it never gets the second `0x53`.
- **Detached base battery (B14, 26.11.2026) [D]:** the base battery exists only while the base is attached. Without the power supply `BAT2` the daemon sends no `0x53` to KIP, and the channels `28B7BD85/102 – 108` are invalid at once, also where they came from the kernel attributes. When `BAT2` is back it is asked at the next read; failures counted while the base went away are dropped. BAT1 (SAM) is not gated. As Windows, where the base's battery client is removed with the base [C, `knowledge/sb3-detach-and-base.md`]. Only the SB3 13" and 15" blobs of the corpus read one of these channels (`/103`, dGPU limit only) [C, 26.11.2026]. Before this, a missing attribute made the daemon ask the detached base, three times without an answer, and log "not sent again until the next resume" [M, SB3, 26.11.2026]. Tested on a fake sysfs. Two real detaches with this code on 26.11.2026 showed no EC warning, but the base was off for only about 11 s each, too short to tell (the old code warned after 27 s and 2 min) [M]; a longer detach is open.
- **No retry forever:** a request that gets no answer three times in a row is not sent again until the next resume (one more try then). The input stays invalid and one warning says which command it was. A missing debug device is not counted (it is reported once at start).
- **Adapter rating 0 on AC [H]:** the Pro 9 report tool read 0 mW with the charger connected, the daemon 38745 mW (a 39 W supply). On battery 0 is valid: the blob reads exactly 0 as "no adapter" (psys PL1 100 W, battery mode 1). On AC it cannot be true, so the daemon counts it as unknown (invalid), asks again after 5 s up to three times, then every 60 s. Cause of the first 0 not shown; the report tool had just sent a `0x69` that timed out (which it no longer sends there) [H]. `0x80000000` ("not present") stays invalid.
- **Zero fields on the Pro 9 [H]:** the `0x53` answer is complete (21 bytes), but available input power (`/8`) and rest of platform (`/6`) read 0 on AC. The daemon uses them as 0. In the Pro 9 blob they reach only PL4: with a peak power of 142 W the clamp (130 W) hides them; with a low peak power (e.g. 40 W) a 0 in `/8` lowers PL4 a lot (peak 40 W: PL4 34 W with `/8` 0, 93 W with 45.7 W). Whether Windows takes these two fields from the `0x53` EVENT instead of the request is not shown (the event has the same layout on the SP7/Laptop 3, surface-aggregator-module#29). The daemon does not listen to events through the debug device (it needs the event ioctls, not used elsewhere): open point, to be decided with a Pro 9 `0x53` event log.
- **Transport, in this order:**
  1. **A kernel interface, when present.** Planned as an SSAM client driver in linux-surface. It exposes the same values as sysfs attributes and is upstreamable (OD-3).
  2. **The debug character device `/dev/surface/aggregator`.** This is interim: it needs root and the `surface_aggregator_cdev` module. Without either transport, these channels are invalid and the result is the Windows fallback (psys 23 / 34.5 W on the SB3, `engine.md` 7).

## 7. Polling and cost (NFR-4)
- **Sensors:** sysfs reads once per second. EC requests as in 6. No busy waiting.
- **Only what the blob reads:** the temperature adapter reads only the `surface_thermal` channels the blob uses (each read is an EC
  request, ~2 ms; all 18 on the SB3 took ~30 ms per second [M]). Other adapters are not called at all when the blob reads none of
  their channels.
- **Read failures:** a failing read makes that channel invalid for this cycle. After 10 failures in a row, the adapter logs once and retries every 30 s [D].

## 8. Inputs of the newer blobs (phase 6)
What Windows sends is in `../research/09-newer-blobs.md` 4. Each adapter declares its channels only where Linux has the source; a
missing source keeps the device in dry-run (`supervisor.md` 2.5). None of these channels is read by the SB3 blobs.

### 8.1 Display brightness in percent (`1FB53D4C/42`)
- Windows sends the brightness setting of the current monitor in percent.
- Linux: `round(100 · brightness / max_brightness)` of the panel backlight. With several backlights: type `firmware` before `platform`
  before `raw`. No backlight: not provided.
- [H] The Windows percent and the raw backlight scale may not be linear to each other.

### 8.2 External display, camera, Surface Docks (`8060B17F/2, 3, 9`)
- **External display:** 1 if any DRM connector other than `eDP`, `LVDS` or `DSI` has `status` `connected`, else 0. Provided where
  `/sys/class/drm` exists.
- **Camera:** 1 if a video4linux node is in use. Per node, the nearest ancestor device with runtime PM decides when its
  `power/control` is `auto`: `runtime_status` `active` means in use. Otherwise (runtime PM off) a process with the node open counts
  (`/proc/*/fd`, root sees all).
- **Docks:** number of USB devices with vendor `045e` and product `0900`, `0857` or `09a9`.

### 8.3 Battery current (`28B7BD85/12`)
- Windows: `_BST` rate (mW) · 1000 / present voltage (mV), positive while the state is exactly "discharging", else negative.
- Linux: `current_now` (µA) if present, else `power_now / voltage_now`; magnitude in mA, positive if `status` is `Discharging`,
  else negative. Integer mA.

### 8.4 Battery EWMA level and max-charge flag (`28B7BD85/13, 16`)
- SSAM request, target category `0x02` (BAT), target 1 (SAM), instance 1, command `0x1A` (level) and `0x69` (flag), one-byte answer.
  The EC also sends events with the same command IDs when the values change.
- Interim transport: the debug device (section 6), every 10 s and after resume [D]. Without the debug device the channels are
  not provided. A kernel attribute next to the `dptf` group is planned (OD-3).
- [M] `0x1A` answers on the Surface Pro 9 (value 20 at 90 % charge, tester). `0x69` gives no answer there, and its blob does not read `/16`: the daemon sends only the query whose channel the blob reads.

### 8.5 Standby phase (`DEF1AB69/100`)
- Windows: the Surface ACPI platform extension forwards the Microsoft low-power `_DSM` calls (`Notify(SAPD, 0xC0 + n)`) and maps them
  to 1 (display off), 2 (sleep), 3 (maintenance), 5 (legacy sleep). While `GUID_GLOBAL_USER_PRESENCE` reads "not present", and for
  every other notification, it sends 4.
- Linux: constant 4. Idle display-off on Windows follows "not present" and so stays 4; 2, 3 and 5 occur while the daemon is
  suspended.

### 8.6 USB source power (`049E36F2/9`)
- Windows measures the power out of the USB-C ports (Snapdragon models).
- Linux has no measurement. Upper bound: the sum over Type-C ports in source role with a partner. With a PD contract: the largest
  fixed PDO the port offers (`usb_power_delivery/source-capabilities`); else 5 V × the current of `power_operation_mode`
  (`default` 900 mA, `1.5A`, `3.0A`). An overestimate lowers the CPU limit. Provided where `/sys/class/typec` exists.

### 8.7 On battery (`1FB53D4C/31`)
- Windows: the Surface GPU power controller (Laptop Studio) sends 1 on DC and 0 on AC, from `GUID_ACDC_POWER_SOURCE`.
- Linux: 1 if the `Mains` supply is offline, 0 if online, invalid if there is none.

### 8.8 Proximity sensor (`87008365/113`)
- Windows: the SMF sensor client reads a proximity custom sensor and sends 0 (no valid reading), 1 (valid, nothing near) or 2
  (something near). The blob allows a 2 K higher skin limit only at 1.
- Linux has no driver for this sensor. The adapter sends 0, what Windows sends without a valid reading.

### 8.9 Xbox controller (`87008365/5`)
- Windows: "controller active (Xbox connection)" from the SMF client. At 1 the blob allows a 3 K higher skin limit.
- Linux: 1 while a joystick input device from vendor `045e` or with "Xbox" in its name exists, else 0 [H: the Windows criterion].

### 8.11 Averaged CPU temperature (`87008365/112`)
- Windows: the Intel SoC client reads the package thermal status (MSR 0x1B1, bits 22:16) once per request and converts it as (TjMax - readout) x 10 + 2731 in 0.1 K. It has no averaging or window; the same register and field also feed channel 102 of the interface `2E8B14C2` [C, descriptor table and read path of the Pro 10 client]. The name "averaged" comes from the blob's name table. A filter in the SMF core was not found [M]. The blob uses the value only for the fan noise floor (`B620798F/121`).
- Linux: the `coretemp` "Package id 0" temperature, which reads the same register, in 0.1 K, with the same 1 K steps (deviation DV-7, now expected to be none). Provided where `coretemp` exists.
- `87008365/200` (estimated ambient temperature) is not provided: in every blob that reads it (Pro 10, 10 5G, 11 Snapdragon, 12", Laptop 13") it reaches no device output, only telemetry sections and unread nodes (`../research/09-newer-blobs.md` section 6) [C].

### 8.10 dGPU power and temperature (`1FB53D4C/3`, `2E8B14C2/101`)
- Windows: the Surface GPU power controller gets both from the NVIDIA driver.
- Linux: NVML (`nvmlDeviceGetPowerUsage`, `nvmlDeviceGetTemperature`), at most every 2 s, through the idle gate shared with the
  clock-cap stand-in (`dgpu-standin-service.md` 4): a runtime-suspended GPU gives 0 mW and an invalid temperature without a query, the
  pause after idle samples keeps the last values, a missing GPU is invalid.
  Provided where an NVIDIA display controller exists.
