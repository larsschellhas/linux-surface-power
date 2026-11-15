# Spec: input adapters

Version 0.1, 14.11.2026. Implements FR-I1 – FR-I8, NFR-11. Tests: AC-I1 – AC-I3. Markers as in `blob-format.md`.

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
| | 101, 102 | GPU, CPU temperature | 102: hwmon `coretemp` "Package id 0"; 101: not provided | as above |
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
| | 12 | battery margin / current (newer blobs) | not provided (phase 5) | |
| `8060B17F` posture | 1 | posture 1 – 5 | `surface_dtx` mode + lid (section 4) | integer |
| `5D0EE597` lid | 1 | lid state | lid switch (section 4) | 1 = open, 2 = closed [H] |
| `1FB53D4C` power/state | 41 | display brightness in millinits | not provided by default (section 5) | |

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
- **Read failures:** a failing read makes that channel invalid for this cycle. After 10 failures in a row, the adapter logs once and retries every 30 s [D].
