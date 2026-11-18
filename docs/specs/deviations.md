# Deviations from Windows behaviour

Version 0.1, 14.11.2026. Implements G-3, NFR-10, AC-X3. Every known or possible difference between this implementation and Windows'
SMF is listed here. Intended improvements are opt-in and off by default; none exists yet (ideas in `../requirements/ideas.md`).

## 1. Unavoidable (no Linux counterpart)

| ID | Difference | Effect | Default |
|---|---|---|---|
| DV-1 | Temperatures are polled every second. Windows reads them only when the EC trip point (42 °C on the SB3) is exceeded, and holds the last value below it. | None for control: every controller is OFF far above 42 °C. Below the trip point, Linux sees current values where Windows holds an old one. | always |
| DV-2 | KIP/base temperatures (600 + iid) are not available on the SB3 with current kernels. They need a registry node and a `surface_temp` match entry for KIP (prototype works, 15.11.2026). | Without them, the dGPU skin loop reads an invalid input. That output is log-only anyway. | until the kernel patches land |
| DV-3 | Display brightness in nits is not available. | PL4 uses 3 W display power (the Windows fallback without a value). At most ~3 W difference in PL4. | channel invalid; optional profile value |
| DV-4 | Slider states 8 – 10 (game mode, mixed reality, service flag) do not exist on Linux. Low-power on AC maps to Recommended. | Linux never selects these states. | fixed mapping (OD-4) |
| DV-5 | The dGPU limit, WTMF and fan outputs have no Linux target. On the SB3 (GTX 1660 Ti, driver 595), `nvidia-smi -pl` is refused with or without persistence mode, and Dynamic Boost (`nvidia-powerd`) needs Ampere or newer; the driver reports it as not supported. | Computed and logged only. A clock-cap stand-in is a backup concept (I-4). | log-only |
| DV-6 | `0x53` data comes from requests instead of EC events. | Values are fresher than on Windows (where events sometimes did not arrive). | always |
| DV-7 | `87008365/112` (called "averaged" CPU temperature) is the instantaneous `coretemp` package temperature. The Windows SoC client takes one reading of the same register (package thermal status, MSR 0x1B1, bits 22:16) per request and converts it with TjMax to 0.1 K; it holds no average and no window [C, client driver of the Pro 10]. Linux gives the same reading with the same 1 K steps. A filter in the SMF core was not found [M]. | Expected to be none. Only the fan noise floor `B620798F/121` above ~71 °C uses it. | on |
| DV-8 | PL1/PL2 (and the PL1 time window) go to the MSR copy (`0x610`, powercap). Windows writes the MMIO copy (MCHBAR + `0x59a0`) with enable and clamp set; `intel-rapl-mmio` needs the processor thermal device, which is absent on the SB3 and a Pro 10 (`outputs.md` 3.3). | The lower copy is in force: lowering works as on Windows; raising above the firmware's MMIO value has no effect where that copy is enabled (Pro 10: 28 W instead of 30 W; Pro 8: 30 W instead of the blob's 35 W [M]; Pro 8 Windows value 35 W is derived, not measured). On the SB3 15" the firmware leaves the MMIO copy disabled, so the MSR copy alone is in force [M]. The MMIO copy is neither changed nor restored; report, log (every 60 s, warning when an enabled MMIO half is below the computed value) and `status.json` show it. Windows' own effective limit also includes the firmware MSR value [H]. | always, until a Linux path exists |
| DV-9 | PL3 (`0B7B5350/59`) is written only where a profile switches it on. Windows writes it on every Intel device with a CPU client. Verified devices without a matching profile log it only. | Peak platform power keeps the firmware value there (on the SB3 that would be 65 W instead of 100 W on AC). | per profile (`outputs.md` 4) |

## 2. Unknown details, chosen by us

| ID | Point | Our choice | How to verify |
|---|---|---|---|
| DH-1 | Which input changes trigger an asynchronous run | any change in value or validity of an input the instance reads | replay comparison with Windows traces |
| DH-2 | When the timer starts and stops | after every run, it follows the `active` flag of that run; first timer run one period after the start | Windows ETW trace of the SMF core |
| DH-3 | Timer run and input change in the same second | one timer run | as above |
| DH-4 | Internal nodes shared between instances 0 and 1 | shared | no blob uses instance 0 |
| DH-5 | Input readings outside the channel range | invalid | Windows client behaviour on range errors |
| DH-6 | Cores 3.141 – 3.158 | `classic` variant | read such a core, if one turns up |
| DH-7 | Charge rate while charging | 0 | read the power-tracker client |
| DH-8 | Elapsed time of the first EWMA update (engine 5.5). Windows counts it from loading the blob. | counted from the first run, so the first update keeps the default | Windows ETW trace |
| DH-9 | EWMA in core 3.204.23 (Pro 10) has no `Δt ≥ τ` rule and overshoots after long gaps | the rule of cores ≥ 3.205.6 for all cores | differs only when a run comes more than τ after the last |
| DH-10 | State of the newer blocks (engine 5.1 – 5.5) on resume | kept | read the core's resume path |

## 3. Safety additions (not in Windows)

| ID | Addition | Default |
|---|---|---|
| DS-1 | Hard limits in the output stage (`outputs.md` 2) | on, cannot be widened |
| DS-2 | Emergency rules (`supervisor.md` 4) | on |
| DS-3 | Yielding to foreign changes (`supervisor.md` 5) | on |
| DS-4 | Firmware values before suspend | on |

## 4. Stand-ins (a different mechanism for Windows behaviour)

| ID | Stand-in | For | Default |
|---|---|---|---|
| DO-1 | dGPU clock cap regulated to the measured dGPU power (`outputs.md` 6) | output 201, which has no Linux power-limit target (DV-5) | per profile option `dgpu_clock_cap`; on for the SB3 (parity: 50 W instead of the driver's 65 W) |
