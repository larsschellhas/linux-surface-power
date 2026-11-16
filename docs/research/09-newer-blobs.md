# Newer blobs: block types, corpus check, missing inputs (phase 5)

Phase 5 (15.11.2026) added the block types of the newer SMF blobs. Their behaviour was read from the SMF cores 3.140 to 3.205.23 (x64) and is
specified in `../specs/engine.md` 3.6 and 5, and in `../specs/controller.md` 6. This file summarises the result for the blob corpus and what
is still missing on the Linux side. Markers as in `README.md`.

## 1. Block types [C]

| GUID | Microsoft name | Spec | Behaviour in one line |
|---|---|---|---|
| `76536F66` | CONDITIONALTABLE | engine 5.1 | first row whose cells all match the inputs gives `y`; otherwise the last result |
| `0DBF7FAB` | HYSTERESISTABLE | engine 5.2 | step switch over overlapping bands; holds inside an overlap |
| `A58AF9AA` | HYSTERESIS | engine 5.3 | quantiser with a hysteresis of one step |
| `3D42677C` | MULTIPLYDIVIDE | engine 5.4 | product of the inputs, each optionally inverted, clamped |
| `54C09C3D` | EWMA | engine 5.5 | time-based exponential smoothing |
| `A0CE482E` | (SB3 13" only) | engine 5.6 | weighted mean plus offset, clamped |
| `0D24F500` | VARIABLEPID | controller 6 | the same controller core with one set and the setpoint from an input |

- **Expressions with channel references** (e.g. table bounds `VN208 + 3 K`) are evaluated in every run. If a referenced channel has no
  valid value, the block skips the run (engine 3.6). In the corpus only lookup tables use them.
- **Findings that differ from the earlier survey:**
  - The conditional table does not check its inputs. An invalid input takes part with its last value.
  - The hysteresis table writes nothing while its input is invalid.
  - In EWMA only the first input has an effect. Core 3.204.23 (Pro 10) lacks the rule `Δt ≥ τ ⇒ y = x` of later cores (DH-9).
- **MPTF configurations** (2025/26 models) are now rejected with a message that says so, instead of a parse error.

## 2. Corpus result [C]

- **All 41 SMF blobs** build without unsupported lines, in both controller variants.
- **A heat-up sweep** (skin 30 → 60 °C in 30 min, local test) runs every blob:
  - without errors and with finite outputs;
  - in every blob at least one controller becomes active;
  - in every blob with PL1, PL1 drops.
- **The 12 MPTF files** are recognised as such.
- **Not compared with Windows:** no traces exist for any device other than the SB3, so the numbers of the newer blobs are checked only
  for plausibility.

## 3. Core version and controller variant per device [C, package contents]

The variant follows the core in the device's driver package (`../specs/engine.md` 3.5).

| Variant | Devices (core) |
|---|---|
| `classic` | Book 3, Laptop Go 1 (3.140); Pro 8, Go 3, Laptop Studio 1 (3.137); Laptop 4 AMD (3.78); Laptop 3 AMD (2.51 [H: the state machine of 2.51 was not compared]) |
| `direct` | Laptop Go 2 (3.159); Pro 9, Laptop 5 (3.180); Go 4, Laptop Go 3 (3.202); Laptop Studio 2 (3.204.2); Pro 10 (3.204.23); Laptop 6 (3.205.6); Pro 10 5G (3.205.11); Laptop 7 Intel, Pro 11 Intel (3.205.14); Pro 7+, Laptop 4 Intel (3.205.17); Laptop 7 5G (3.205.23); arm64: Pro 9 5G (3.181), Laptop 7 / Pro 11 Snapdragon (3.205.8), Laptop 13" / Pro 12" 1st Ed. (3.205.17) |

- Without a device profile the daemon uses `direct`. Every `classic` device has a profile (untested):
  - Book 3, Pro 8, Laptop Studio and Go 3 by product name, as the kernel matches them (`surface_gpe`, `acpi/battery.c`,
    `intel/hid.c`);
  - Laptop Go 1, Laptop 3 AMD and Laptop 4 AMD by SKU. The SKUs come from a public catalogue of the Surface driver packs (OSDeploy
    `surface.json`, `SystemId`). They agree with the SKUs the kernel and linux-surface match for the Laptop 3/4, and with the SB3 15"
    here (`Surface_Book_3_1899`) [M].
  - Laptop 4 Intel shares its product name with the AMD model, so the AMD profile must match by SKU.

### 3.1 The arm64 cores [C]
The three arm64 cores (3.181, 3.205.8, 3.205.17) were checked separately:
- **GUID → type:** the same as in the x64 core of the same generation. 3.181 has one extra type (`061C64E3`) that no blob uses.
- **3.205.8 and 3.205.17:** every block's run function carries the same source line numbers in its assertions as the x64 cores of the
  same source generation (3.205.6 / 3.205.11 and 3.205.17). They are built from the same source file.
  - Spot check: EWMA in 3.205.8 has the `Δt ≥ τ` rule.
- **3.181** (Pro 9 5G) has no assertions yet. Each run function was matched to its 3.180 x64 counterpart by a normalised code comparison.
  - The three newer blocks the Pro 9 5G blob uses were then read in full: conditional table, hysteresis table and quantiser. They
    behave as specified, with the same tolerance of 0.001.
  - The quantiser truncates instead of rounding down; after clamping the argument is never negative, so both give the same result.
- **Not checked:** whether the arm64 compiler contracts multiply-adds into fused operations. That can change the last bit of a result,
  not the behaviour [H].

## 4. Inputs the newer blobs need

Inputs that reach PL1, PL2 or psys in some blobs but have no Linux source in the daemon yet. While such an input is missing, the daemon
stays in dry-run on that device (`../specs/supervisor.md` 2.5), even though the blob itself is fully supported.

Sources of the facts:
- the blobs (macro names, value ranges, how the blocks use the value) [C];
- the Windows clients of the Laptop 7 Intel, Laptop 7 Snapdragon and Laptop 4 AMD driver packages (which client provides the value and
  what it measures) [C];
- the public ACPI dumps of linux-surface (`acpidumps`) for the affected devices [C];
- this SB3 (whether Linux has a matching sysfs entry) [M].

The Linux sources are candidates until an adapter has been compared on a device that needs it [H].

| Channel | Microsoft name | Blobs reading it / reaching a limit | What Windows sends | Linux source |
|---|---|---|---|---|
| `1FB53D4C/42` | `I_DISP_PERCENT1` | 29 / 29 | Display brightness in percent, 0–100, from the Windows power setting for the current monitor brightness. The blob turns it into display power itself (e.g. 43.73 · % − 1554.4 mW). | `/sys/class/backlight/*`: `brightness` / `max_brightness` · 100 [H: Windows percent and the raw backlight scale may not be linear to each other] |
| `8060B17F/2` | `I_EXT_DISPLAY` | 37 / 27 | 1 while a wired external monitor is connected, else 0. Reported by the user-mode service. | `/sys/class/drm/card*-*/status` = `connected` for any connector except `eDP`/`LVDS`/`DSI` [M: `DP-1 connected` here] |
| `8060B17F/3` | `I_CAMERA` | 34 / 25 | 1 while an application uses a camera, else 0 (Windows sensor-activity monitor). | Runtime-PM state of the camera device: `power/runtime_status` = `active` while streaming [M: a USB webcam here shows `suspended` when idle]. The built-in IPU cameras need a check on a device. |
| `8060B17F/9` | `I_SURF_DOCK` | 19 / 11 | Number of attached Surface Dock USB hubs: Microsoft vendor 045E with product 0900, 0857 or 09A9. It counts up on attach and down on removal. | USB devices with `idVendor` 045e and one of these `idProduct` values below `/sys/bus/usb/devices` |
| `28B7BD85/12` | `I_BCDR1_I` | 29 / 20 | Battery current in mA, signed. The Surface battery client computes it from the EC's `_BST` data: rate (mW) · 1000 / present voltage (mV). It is positive while the battery state is exactly "discharging" and negative otherwise. The blobs smooth it (±15000 mA) and compare it with fractions of the battery's C rate. | `power_supply`: `power_now` / `voltage_now` (µW / µV → mA), positive when `status` is `Discharging`, else negative. `current_now` where a driver has it. |
| `28B7BD85/13` | `I_BDR_EWMA1_LVL` | 24 / 16 | A level from the EC: "battery discharge rate EWMA level", one byte. The battery client reads it with the SAM battery command `0x1A` and re-reads it on the SAM battery event with the same ID. The blob uses it to select a controller set. | SAM battery (target category 2), command `0x1A`, one-byte response, through the same request path that `surface_battery` uses for `0x53` (kernel#195). Until a kernel patch adds it to sysfs: the SSAM debug device (root) [H: not tried on an affected device]. |
| `28B7BD85/16` | `I_BAT1_MAX_CHARGE` | 12 / 9 | A flag 0/1 from the EC: the battery reached its maximum charge level for the current state. SAM battery command `0x69`, one byte, re-read on the SAM battery event `0x69`. In the blobs a conditional table matches it as exactly 0 or 1; the bands up to 25001/55001 belong to the input power limit next to it. | SAM battery command `0x69`, as channel 13 |
| `DEF1AB69/100` | `I_MS_NOTIFY` | 24 / 16 | The Modern Standby phase, from the Surface ACPI platform extension driver (ACPI `MSHW0299`, device `SAPD`). The firmware forwards each call of the Microsoft low-power `_DSM` (UUID `11e00d56-…`, function n) as `Notify(SAPD, 0xC0 + n)`. The driver maps: display off (3) → 1; sleep entry (5) or Modern Standby entry (7) → 2 on a Modern Standby system, else 5; sleep exit (6) → 3 if the last level was 2 (maintenance), else 5; everything else → 4. While a Windows power setting it registers reads 0, the level is 4 [H: the setting's meaning is not identified]. The blob macros name the levels: 1 screen off, 2 sleep, 3 maintenance, 4 normal (global PL1 maximum), 5 legacy sleep. Pro 9 Intel, Pro 10, Laptop 6 and others have this `_DSM` forwarding in their DSDT (`acpidumps`). | Constant 4 while the daemon runs and the display is on. 1 while the internal panel is off (backlight `bl_power` ≠ 0 or the eDP connector off). 2/3/5 only occur during suspend, where the daemon does not run. Linux's `s2idle` calls the same `_DSM` functions on suspend [C: `drivers/acpi/x86/s2idle.c`]. |
| `049E36F2/9` | `I_USB_SOURCE_PWR` | 8 / 8 | Power the device supplies through its USB-C ports, mW. The Surface service reads the energy-meter channel `USBC_TOTAL`, which the Qualcomm platform driver provides on the Snapdragon models, and sends it through the power tracker. A block subtracts it from the input power limit. | No direct Linux source known. Candidate as an upper bound: per port with a sink partner, the negotiated contract (`/sys/class/usb_power_delivery`) or the Type-C current level (`/sys/class/typec/port*/power_operation_mode`) times 5 V [H]. An overestimate lowers the limit, so it errs on the safe side. |
| `1FB53D4C/3` | `_I_DGPU_PWR` | 2 / 2 | dGPU power (Laptop Studio). | NVML / `nvidia-smi --query-gpu=power.draw` |
| `1FB53D4C/31` | not named | 2 / 2 | range 0–8, Laptop Studio dGPU blobs. The interface also carries display brightness and NPU use; on Windows the Surface service and the SMF clients write it. The Surface service of these devices talks to the NVIDIA driver for Dynamic Boost [H: the channel is related to it]. | open |
| `87008365/5`, `/113` | composite sensors | 2 / 2, 1 / 1 | Defined in the blob itself by 14/19-field channel lines (`../specs/blob-format.md` 5.2a). The interface also carries `I_CPU_TEMP_AVG` and `I_AMBIENT_AI`; the SMF clients and the Surface service write it. | open: the definition lines need analysis; possibly computed by the core from other channels [H] |

- **Ready for adapters:** display brightness, external display, camera, dock, battery current, and `I_MS_NOTIFY` (4, or 1 with the
  panel off).
- **Need a kernel addition first:** channels 13 and 16. Both are one-byte SAM battery queries (`0x1A`, `0x69`) next to the DPTF query
  that kernel#195 exposes. Until then the SSAM debug device can serve them with root.
- **Approximation only:** USB source power on the Snapdragon models. Linux has no measured value; the PD contract gives an upper bound.
- **Open:** `1FB53D4C/31` and the composite sensors (Laptop Studio, Pro 9 Intel, Laptop 5).

## 5. Start-up observation [C, engine run]

With all inputs set at once and then constant, some blobs need two evaluation runs to settle, because a block reads a node that a later
stage writes:
- Pro 7+: PL1 is 5.0 W after the first run and 15.0 W after the second.
- Laptop 7 Intel, Pro 11 Intel, Laptop 13" / Pro 12" 1st Ed.: one WTMF output 50 → 100.
- The SB3 and all other blobs settle in one run.

The scheduler runs an instance only when an input changes or the timer runs, so a device with steady inputs would keep the first-run
value. On Windows, inputs arrive one by one at boot, which gives several runs [H]. Repeating the first run until the outputs
settle is noted as idea I-12 (`../requirements/ideas.md`).
