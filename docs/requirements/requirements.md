# Requirements

Version 0.2, 14.11.2026. Status: agreed scope and decisions (section 6).

Keywords: **MUST** = required for the release that targets the scope level; **SHOULD** = expected unless there is a documented reason;
**MAY** = optional. Each requirement has an ID; acceptance criteria refer to it (`acceptance-criteria.md`).

## 1. Purpose and scope

### 1.1 Vision
`linux-surface-power` enables clean power and thermal management for **all Microsoft Surface devices** on Linux. "Clean" means that the device
- uses the performance it has;
- stays within the temperatures and power budgets its manufacturer designed for;
- never ends up in a worse state than the firmware defaults.

### 1.2 Scope levels

| Level | Devices | Status |
|---|---|---|
| **L1: reference device** | Surface Book 3 15" (Intel Ice Lake, NVIDIA dGPU) | first target |
| **L2: SMF, x64** | all Surface devices controlled by Surface SMF with an x64 CPU (see `../research/04-smf-device-survey.md`) | mid-term target |
| **L3: SMF, arm64** | Snapdragon SMF devices (Pro 9 5G, Laptop 7, Pro 11, Laptop 13" 1st, Pro 12" 1st) | after L2, depends on interfaces |
| **L4: other frameworks** | Windows MPTF devices (2025/26): in scope long-term. Intel DTT devices: stay with thermald if it covers them fully (to verify) | long-term |

The design MUST NOT block L3 and L4. In particular the loader and engine MUST allow a second blob format (MPTF) and output types other than RAPL (WTMF stages, fan).

### 1.3 Out of scope for L1/L2
- **Fan control on devices where the EC owns the fan** (e.g. the SB3: no working fan command). Devices whose blob drives the fan (Snapdragon SMF, possibly MPTF) MUST be considered in the design (FR-O7).
- **Setting the power profile.** `platform_profile` belongs to the user or to `power-profiles-daemon`.
- **dGPU power limit enforcement.** There is no Linux interface for the SB3 GPU (see FR-O5).
- **Improvements over Windows behaviour** (smoother control, CPU temperature loop). They are a later, opt-in topic (G-3). Ideas are collected in `ideas.md`.

## 2. Goals and principles

| ID | Goal / principle |
|---|---|
| G-1 | **Windows parity first.** On a supported device, the limits set on Linux match what Windows' SMF sets for the same inputs. |
| G-2 | **Firmware-driven.** All device-specific behaviour comes from the device's own SMF configuration blob. There is no hand-tuned per-device table in the code. |
| G-3 | **Deviations only by explicit choice.** Behaviour that differs from Windows (improvements, workarounds) is off by default, named, and documented. |
| G-4 | **Fail safe.** On any error, unknown input or exit, the device returns to its firmware default limits. |
| G-5 | **Upstream-ready.** Interfaces, packaging and code quality allow adoption by linux-surface. Where a kernel interface is missing, it is proposed upstream rather than worked around permanently. |

## 3. Functional requirements

### 3.1 Device detection (FR-D)
| ID | Requirement |
|---|---|
| FR-D1 | The software MUST identify the device (DMI product name, SKU/product version) and the control framework (SMF present via ACPI `MSHW0187` and a blob). |
| FR-D2 | On a device without a supported framework or blob, the software MUST NOT write any limit. It MUST exit or idle with a clear message. |
| FR-D3 | The software MUST keep a list of supported and tested devices. On an untested SMF device it MUST default to dry-run (FR-M4), unless the user enables control explicitly. |

### 3.2 Blob acquisition and parsing (FR-B)
| ID | Requirement |
|---|---|
| FR-B1 | The software MUST read the blob from the UEFI variable `SMF_DATA_BLOB-a3d05fa9-db5e-490c-956e-a57384a4b70b` (delivery form B). |
| FR-B2 | The software MUST accept a blob from a file path. This is for form C devices, the Laptop 3 AMD (form A), tests, and blobs extracted from Microsoft firmware packages. |
| FR-B3 | The software MUST decode all known delivery forms: A (uncompressed with header), B (header + LZNT1) and C (raw LZNT1). |
| FR-B4 | The software MUST parse all known text forms: plain CSV, bracketed arithmetic expressions (numbers, + − * /, brackets), `{GUID}_N` channel references, and macro source (`//`, `#macro`, `%NAME%`, built-in `VN<n>`). Expression evaluation MUST NOT execute arbitrary code. |
| FR-B5 | The software MUST build the block graph: input channels, output channels, algorithm lines with stage, instance and divider, and connections. It MUST validate it with the same checks as the Windows core (e.g. U_min ≤ U_on ≤ U_off ≤ U_max, min ≤ default ≤ max, kind < 4). |
| FR-B6 | On a parse or validation error the software MUST NOT control (G-4) and MUST report the line and the reason. |
| FR-B7 | The software MUST map algorithms by GUID, never by internal type number. |
| FR-B8 | Unknown algorithm GUIDs MUST be reported. A blob with an unknown GUID on a path to an output MUST NOT be used for control. |
| FR-B9 | The software SHOULD export the parsed graph in a readable form (text and diagram) for review. |

### 3.3 Evaluation engine (FR-E)
| ID | Requirement |
|---|---|
| FR-E1 | The engine MUST implement every algorithm type used by the blobs of the supported devices, with the semantics of the Windows core: lookup tables, maps, arithmetic, min/max/select, pass-through/caps, thresholds and hysteresis, temperature bands, PID with parameter sets and, for newer blobs, variable PID, EWMA and conditional tables. |
| FR-E2 | The engine MUST reproduce the timing: evaluation instances of 1000 and 5000 ms, per-line dividers, stages in ascending order and lines in file order, timer start/stop around active controllers, and evaluation on input changes. |
| FR-E3 | The engine MUST track validity per value. A block with an invalid or missing input MUST output its default, as the Windows core does. |
| FR-E4 | The engine MUST implement both controller variants: the state machine and first step of cores ≤ 3.140 and of cores ≥ 3.159. The variant MUST be selectable per device, with the default taken from the device's package core version. |
| FR-E5 | The engine MUST reset all blocks on resume from suspend and hibernate, like the Windows D0 entry. |
| FR-E6 | The engine MUST be deterministic: the same blob and the same input sequence give the same outputs. It MUST be runnable offline (no hardware) for replay and tests. |
| FR-E7 | Numeric behaviour (units mW / 0.1 K, rounding of set selectors, clamping) MUST match the Windows core within the tolerances in the acceptance criteria. |

### 3.4 Inputs (FR-I)
| ID | Requirement |
|---|---|
| FR-I1 | The software MUST provide every input channel the blob references, from Linux sources, with a defined mapping (`../research/05-linux-interfaces.md`). |
| FR-I2 | **Temperatures:** from hwmon (`surface_temp`/`surface_thermal`), mapped by sensor instance (SAM 500 + iid, KIP 600 + iid). They are read every 1 s, and readings outside the channel's min/max range are marked invalid. |
| FR-I3 | **Slider state:** derived from `platform_profile` and the power source through a **fixed, documented mapping** (OD-4): `low-power` → Recommended (on battery: energy saver), `balanced` → Recommended, `balanced-performance` → Better performance, `performance` → Best performance, each × AC/battery. The mapping is part of the device profile, not a user setting. |
| FR-I4 | **Platform power data** (`0x53` fields, PSU size, charge level, source): requested from the EC on start, on power-source changes and periodically. Missing data MUST be treated as invalid (FR-E3), not as zero. |
| FR-I5 | **Battery** charge level and discharge rate: from `power_supply`, in the unit the blob expects (mW or mA). |
| FR-I6 | **Posture and lid:** from `surface_dtx` and the lid switch where the blob uses them. |
| FR-I7 | **Display brightness:** where the blob uses it, from the backlight class, converted to the expected unit. |
| FR-I8 | An input the software cannot provide MUST be reported once at start and MUST stay invalid. |

### 3.5 Outputs (FR-O)
| ID | Requirement |
|---|---|
| FR-O1 | Package PL1/PL2/PL4 MUST be written through powercap (`intel-rapl`). psys PL1/PL2 MUST be written through `intel-rapl:1`. |
| FR-O2 | PL3 MAY be written through MSR where allowed. If MSR access is blocked (kernel lockdown), the software MUST skip it and report it, and MUST NOT fail. |
| FR-O3 | Every write MUST be clamped to the output channel's min/max from the blob **and** to hard limits in the code that a configuration cannot widen. |
| FR-O4 | The software MUST write only when a value changes. It MUST detect foreign changes to the limits it owns and handle them (FR-S5). |
| FR-O5 | Outputs without a Linux target (e.g. dGPU limit 201, battery mode 70) MUST be computed and logged, not written. A stand-in (e.g. a GPU clock limit) MAY be added later under G-3. |
| FR-O6 | On arm64 devices, outputs MUST stay unwritten until a target interface is specified. |
| FR-O7 | Where a blob drives the fan or other cooling outputs, the output layer MUST support them as a separate output kind once a Linux interface exists. Until then they are computed and logged (FR-O5). |

### 3.6 Supervision and safety (FR-S)
| ID | Requirement |
|---|---|
| FR-S1 | At start, the software MUST save the firmware values of every limit it may write. It MUST restore them on normal exit, on any unhandled error, before suspend, and when it disables itself. |
| FR-S2 | If the process dies without restoring (e.g. SIGKILL, OOM), a separate reset step (systemd `ExecStopPost`) MUST restore the saved values. |
| FR-S3 | A watchdog MUST detect a hung evaluation loop and restart or stop the service. Repeated failures MUST leave the device on firmware values. |
| FR-S4 | Emergency rules independent of the blob MUST apply: skin temperature or CPU temperature above a hard limit, or repeated invalid core inputs, lead to firmware values. They MUST NOT be stricter than needed to keep Windows-parity behaviour in normal use. |
| FR-S5 | When another process changes a limit the software owns, the software MUST NOT fight it. After a defined number of foreign changes it MUST yield (firmware values or read-only) and report it. |
| FR-S6 | The software MUST refuse to run while a known conflicting service (thermald) controls the same limits. It MUST coordinate through systemd (`Conflicts=`). |
| FR-S7 | A configuration MAY only narrow limits, never widen them beyond the blob and the hard limits. |

### 3.7 Monitoring and user interface (FR-M)
| ID | Requirement |
|---|---|
| FR-M1 | The software MUST log state changes, controller state changes (OFF/READY/ACTIVE), invalid inputs, emergencies and foreign changes to the journal, with priorities. |
| FR-M2 | The software MUST publish a status file readable by any user: state, slider state, active controllers, current limits, main inputs and recent events. |
| FR-M3 | The software SHOULD record a time series (one row per evaluation) for later analysis. It MUST be limited in size and age. |
| FR-M4 | **Dry-run mode:** compute and log everything, write nothing. This is used for untested devices and for validation. |
| FR-M5 | A user-session notifier SHOULD show warnings: battery draining on AC (charger too weak), emergency/off states, and return to normal. |
| FR-M6 | A **replay tool** MUST run the engine on a recorded input series (Linux or Windows measurement) and print the outputs, for comparison with Windows. |
| FR-M7 | A **report tool** SHOULD let users of other devices share what is needed for validation (device, blob found yes/no, format, core variant, inputs available) without sharing private data. |

## 4. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 Fidelity | Behaviour is validated against Windows measurements per device before control is enabled by default on that device. |
| NFR-2 Safety | No code path can leave limits above both the blob's maximum and the firmware value. All writes go through one clamped output layer. |
| NFR-3 Privileges | The control service runs as root only where needed. It MUST drop capabilities to what it uses (e.g. `CAP_DAC_OVERRIDE`, `CAP_SYS_RAWIO` only if MSR is used) and use systemd sandboxing. Tools for reading, replay and reports MUST work without root where the data allows. |
| NFR-4 Resources | Typical CPU use < 0.5 % on one core, memory < 50 MB. No busy waiting. Polling is at most 1 Hz for sensors. |
| NFR-5 Robustness | Missing sensors, missing EC answers, suspend/resume, profile changes and power-source changes MUST be handled without restart. |
| NFR-6 Portability | Works on current linux-surface kernels and distros (Debian/Ubuntu, Fedora, Arch) with systemd. Kernel lockdown MUST be supported, with reduced function where MSR is needed. |
| NFR-7 Testability | Engine and parser are testable without hardware. Every block type has unit tests. Replays of the reference measurements run in CI. |
| NFR-8 Confidentiality and licensing | The repository MUST NOT contain Microsoft code, drivers, firmware or blob content. Public tests use synthetic blobs. Real blobs are read from the device or from the user's own download. The project is licensed MIT OR Apache-2.0 (OD-5). Code from GPL projects MUST NOT be copied in. |
| NFR-9 Maintainability | Clear separation of loader, engine, adapters, supervisor and monitor (see `architecture.md`). Python is the reference implementation; the long-term production version is **Rust** (C as fallback option). The Python code is written so it can be ported 1:1. The behaviour is defined by the specs and tests, not by the language. |
| NFR-10 Documentation | Every requirement links to its acceptance criteria. Every spec links to the requirements it implements. Deviations from Windows (G-3) are listed in one place. |
| NFR-11 Upstream interfaces | Use of debug interfaces (SSAM cdev, raw MSR) is marked as interim. For each, a proper interface is designed so it can go into the Linux kernel (via linux-surface): e.g. an SSAM client driver exposing the `0x53` data and DPTF reads. The userspace side reads that interface when present and falls back to the cdev otherwise. |
| NFR-12 Transparency | Contributions note AI assistance where used. |

## 5. Constraints and assumptions
- **C-1:** SMF semantics are known from static analysis of the Windows drivers and from measurements on the SB3 (`../research/03`). Where
  analysis and measurement disagree, the measurement wins and the difference is documented.
- **C-2:** The core version that actually runs on a device under Windows can differ from the package version. The default variant
  (FR-E4) is a best guess.
- **C-3:** Fields of BAT `0x53` other than battery peak power are mapped by order and value range [H]. Validation is required (A-4 in #2383).
- **C-4:** The dGPU limit and Snapdragon outputs have no Linux target yet.
- **C-5:** The SB3 needs the TMP/FAN registry nodes (kernel#190 or the DKMS module) for its skin sensor.

## 6. Decisions

| ID | Question | Decision (14.11.2026) |
|---|---|---|
| OD-1 | Where the project lives | **Own repository** (`linux-surface-power`, public on GitHub). Integration into linux-surface or `surface-control` can follow later. |
| OD-2 | Language | **Python as the reference implementation; Rust as the long-term production version** (C only if Rust is not accepted upstream). |
| OD-3 | Interface for `0x53` | **SSAM cdev as an interim** solution; in parallel, a kernel SSAM client driver designed for upstreaming into the Linux kernel. |
| OD-4 | Slider mapping | **Fixed, documented mapping** per FR-I3. |
| OD-5 | License | **MIT OR Apache-2.0** (dual, as common for Rust). Kernel parts (e.g. the SSAM client) are written for the kernel tree under GPL-2.0, not in this repository. |
| OD-6 | DTT and MPTF | **MPTF: in scope long-term (L4). DTT: stays with thermald if thermald covers it fully**; to verify before deciding finally. |
