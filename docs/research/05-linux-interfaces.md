# Linux interfaces for SMF inputs and outputs

State on the SB3 (kernel 7.2.15-1-surface, Zorin/Ubuntu 24.04), 14.11.2026.

## Inputs

| SMF input | Linux source | State |
|---|---|---|
| Temperatures, SAM part (channels 501–509, iid 1–9) | hwmon `surface_thermal` (`surface_temp` driver), `temp1…9`, millidegrees | SB3: needs [kernel#190](https://github.com/linux-surface/kernel/pull/190) or the DKMS module. Other devices: registry entry per device |
| Temperatures, KIP/base (600 + iid) | not exposed on the SB3 yet | open |
| Slider state | `/sys/firmware/acpi/platform_profile` (low-power, balanced, balanced-performance, performance) × AC/battery | the mapping to states 1–10 is to be specified; Linux has no "energy saver" |
| `I_IPL`, `I_BAT_PEAK`, `I_BAT_MAX`, `I_BAT_RFHE`, `I_BAT_VNL`, `I_ROP_DYN` | SSAM BAT TC 0x02, CID 0x53, IID 1, as a request with response; separately for SAM and KIP. 21 bytes: @1 u32 mW peak, @5 u32 mW sustained, @9 u16 mΩ, @11 u16 mV, @13 u32 mW available input power, @17 u32 mW rest of platform | [M] works via `/dev/surface/aggregator` (debug cdev, root). The field mapping is [H] except peak power. The event `0x53` only arrives on some source changes, so request it |
| PSU size, charge level, source | DPTF reads `0x0f` ARTG (120 W), `0x0c` PSOC (%), `0x0d` PSRC; `0x0b` PMAX | [M] via cdev |
| Charge level, discharge rate `I_BCDR` | `power_supply` (`surface_battery`): `capacity`, `power_now`/`current_now` | available |
| Posture | `surface_dtx` (base attached/detached, mode), lid switch | available; the mapping to 1…5 is to be specified |
| Display brightness (PL4) | backlight class | which unit SMF expects (nits × 1000?) is [H] |
| External display | DRM connector status | the SB3 blob doesn't use it |

## Outputs

| SMF output | Linux target | State |
|---|---|---|
| 51 PL1, 52 PL2 | `/sys/class/powercap/intel-rapl:0/constraint_0/1_power_limit_uw` (time window `constraint_0_time_window_us`) | available |
| 53 PL4 | `intel-rapl:0/constraint_2` (`peak_power`) | available on the SB3 |
| 54/55 psys PL1/PL2 | `intel-rapl:1` (psys) `constraint_0/1` | available, not locked |
| 59 PL3 | MSR 0x615 | only via `/dev/cpu/*/msr`. **Blocked under kernel lockdown** (Secure Boot with lockdown=integrity on many distros). The SB3 here has lockdown `none` |
| 70 battery mode | – | meaning open |
| 201 dGPU limit | NVIDIA | **not possible**: `nvidia-smi -pl` is "not supported" on the SB3's GTX 1660 Ti Mobile [M]. A clock limit could stand in [H, untested] |
| Snapdragon WTMF, fan | – | not looked at |

## Interfaces that should exist upstream (long-term)
- An SSAM client exposing the `0x53` data and the DPTF reads, e.g. as `power_supply` properties, hwmon or sysfs. The debug cdev is not meant
  for production and needs root.
- PL3 in powercap, if needed at all.
- TMP nodes in the registry for every SMF device, plus the KIP sensors on the SB3.

## Notes
- thermald must not run at the same time; it changes package PL1 itself.
- `power-profiles-daemon` drives `platform_profile` and EPP. Our service must only read the profile, never set it.
- MOK-signed DKMS modules work under Secure Boot on the reference machine.
