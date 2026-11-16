# Spec: input adapters

Version 0.2, 16.11.2026. Implements FR-I1 – FR-I8, NFR-11. Tests: AC-I1 – AC-I3. Markers as in `blob-format.md`.

## 1. Interface
- **Adapter role:** each adapter owns one or more device input channels (`GUID/channel`). On `read(now_ms)` it returns `{channel: value | None}` in blob units. `None` means invalid.
- **Reader:** the daemon calls all adapters once per second and passes the result to `Engine.set_inputs()`. It never passes zero for "missing" (FR-I4).
- **Unknown channels:** a channel that the blob reads and no adapter provides is reported once at start and stays invalid (FR-I8).
- **Overrides:** a device profile (`supervisor.md` 6) may override a mapping. It may not invent values.

## 2. Channel mapping

| Interface | Channel | Meaning | Linux source | Conversion |
|---|---|---|---|---|
| `2E8B14C2` temperature | 501 – 599 | SAM sensor `iid = ch − 500` | hwmon `surface_thermal`, `temp<iid>_input` (on the SB3 `temp9` is labelled `I_VTS1` = channel 509 [M]) | `0.1 K = 2731 + m°C / 100` |
| | 601 – 699 | KIP (base) sensor `iid = ch − 600` | hwmon `surface_thermal` whose SSAM device is `01:03:02:00:02` (target KIP, below the base hub). On the SB3 this needs the registry node and a `surface_temp` match entry for KIP (both not upstream yet, tested 15.11.2026 [M]); otherwise invalid | 0.1 K |
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
- The Linux side of the mapping is confirmed. Whether Windows also reports 3 for a detached display stays [H]. Closing the lid in studio mode changed nothing: `/proc/acpi/button/lid` stayed open, so posture 4 is not seen on Linux this way. Laptop with the lid closed (2) was not tested.

The SB3 15" blob reads only posture 1, through the PL1 and dGPU posture tables. Laptop, the normal case, gives 30 W, which does not limit.

## 5. Display brightness (FR-I7)
- **Use:** channel 41 enters PL4 only, through the estimated display power. Linux has no nits value.
- **Default:** the channel stays invalid. The engine then uses the block default (3 W display power), exactly like Windows without a brightness value.
- **Optional:** a device profile may give `panel_max_nits`. Then `millinits = brightness / max_brightness · panel_max_nits · 1000`. This changes PL4 by at most about 3 W.

## 6. EC requests for platform power data (FR-I4, NFR-11)
The same payload also arrives as an EC event on some devices (surface-aggregator-module#29, values there fit the field names below).
- **Preferred source: kernel attributes.**
  - `power_supply/BAT<n>/dptf_battery/{max_platform_power_mw, max_steady_state_power_mw, high_freq_impedance_mohm, no_load_voltage_mv, available_input_power_mw, rest_of_platform_power_mw}` come from `surface_battery`, which applies the EC event.
  - `power_supply/ADP1/dptf_power/adapter_rating_mw` comes from `surface_charger`. It gives ENODATA while no rating is known.
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
- **Validity:** a failed request makes its channels invalid. They are never 0.
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
  not provided. A kernel attribute next to `dptf_battery` is planned (OD-3).
- [H] Not yet requested on an affected device.

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
- Windows: the Intel SoC client reads the package thermal status (MSR 0x1B1, bits 22:16) [C] and averages it [H: window unknown]. The blob uses it only for the fan noise floor (`B620798F/121`).
- Linux: the `coretemp` "Package id 0" temperature, which reads the same register, in 0.1 K, not averaged (deviation DV-7). Provided where `coretemp` exists.
- `87008365/200` (estimated ambient temperature) is still not provided; in the Pro 10 blob it reaches no output.

### 8.10 dGPU power and temperature (`1FB53D4C/3`, `2E8B14C2/101`)
- Windows: the Surface GPU power controller gets both from the NVIDIA driver.
- Linux: NVML (`nvmlDeviceGetPowerUsage`, `nvmlDeviceGetTemperature`), at most every 2 s and only while another process has an
  NVIDIA device open, as for the clock-cap stand-in (`outputs.md` 6). A runtime-suspended GPU gives 0 mW and an invalid temperature.
  Provided where an NVIDIA display controller exists.
