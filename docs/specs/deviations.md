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
| DV-5 | The dGPU limit, WTMF and fan outputs have no Linux target. | Computed and logged only. | log-only |
| DV-6 | `0x53` data comes from requests instead of EC events. | Values are fresher than on Windows (where events sometimes did not arrive). | always |

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

## 3. Safety additions (not in Windows)

| ID | Addition | Default |
|---|---|---|
| DS-1 | Hard limits in the output stage (`outputs.md` 2) | on, cannot be widened |
| DS-2 | Emergency rules (`supervisor.md` 4) | on |
| DS-3 | Yielding to foreign changes (`supervisor.md` 5) | on |
| DS-4 | Firmware values before suspend | on |
