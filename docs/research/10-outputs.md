# Outputs of the blobs and what they do on Windows

Phase 7 (16.11.2026). The catalogue of every output in the corpus is in [`../devices/README.md`](../devices/README.md); this file
records what is known about the receivers. Markers as in `README.md`.

## 1. Rule for control [D]
A device is ready for control only when Linux writes every output of its blob. A blob that regulates the CPU while its fan, throttle or
safety outputs go nowhere runs with part of its design missing. The daemon enforces it (`../specs/supervisor.md` 2.6). By this rule no
device was ready before 16.11.2026; the SB3 15" lacked only PL3 and PL4, which are measured and switched on now (4).

## 2. Thermal policy (`110DF2A4`, `9BBB8910`) [C, Windows thermal-policy driver]
The Surface thermal-policy driver owns the ACPI thermal zone `TPOL` (`MSHW0187`) and takes these outputs:
- **`9BBB8910/1…n`** (one per device that ACPI method `OPTS` lists, e.g. CPU clusters, GPU, modem, skin): a passive-cooling request in
  percent (100 = no throttling) for that device (`PoSetThermalPassiveCooling`).
- **`9BBB8910/62`** (`O_WTMF_PARK`): the passive limit of the `TPOL` zone in percent.
- **`110DF2A4/1`** (`O_HOT`) and **`/2`** (`O_CRT`), values 0–2: at 2 the driver sets the "hibernate" or the "critical" flag of the
  zone's thermal policy. The 24-byte policy it returns matches the Windows `THERMAL_POLICY` layout (flags at offsets 5 and 6, passive
  limit at 12) [H: identified by the layout]. Windows then hibernates or shuts down.
- **`110DF2A4/3`** (`O_MINPWR2`): not analysed.

On the SB3 15" (engine run, skin sensors 505 and 509 at the same temperature): `O_HOT` is 2 from about 54 °C to 56 °C, `O_CRT` is 2
from about 56 °C up to 100 °C. The daemon's own emergency rule (skin ≥ 60 °C for 10 s) only restores the firmware limits.

On Linux `O_HOT`/`O_CRT` are thermal actions since 16.11.2026: hibernate (else suspend) and power-off, armed in control mode
(`../specs/outputs.md` 7). The throttle requests are written as cpufreq caps per CPU policy and a GPU
frequency cap since 16.11.2026 (`../specs/outputs.md` 8); the ACPI `OPTS` lists in the public dumps show that x86 devices have only the
Processor Aggregator Device (`PAGD`, matched with `/62` [H]), so only the Snapdragon (arm64) devices have per-CPU, cluster and GPU requests; untested on a device.

## 3. Others
- **Power limits (`0B7B5350`):** IHV CPU client (Intel, Qualcomm), AMD SMF driver, GPU power controller (Laptop Studio, 201–208),
  battery client (70 battery mode) [C, GUID references]. Linux writes PL1, PL2 and psys PL1/PL2 on Intel through powercap, PL4
  where powercap reports it and PL3 through MSR 0x615 (both held while their EC inputs are invalid), and the dGPU limit through the
  clock-cap stand-in.
- **Fans and charging (`B620798F`):** the SMF client sends each channel as one SAM command to the EC (FAN and TMP categories); the
  commands, units and a safe test procedure are in [`11-fan-charging.md`](11-fan-charging.md). Since 16.11.2026 the daemon sends the
  limits (101–103, 111–116, 121–126, 306, 312) through the SSAM debug device (`../specs/outputs.md` 9); 201 and 301 are sent by the
  linux-surface kernel (`surface_platform_profile`), and the temperature thresholds 302–311 stay off unless a device profile opts in.
  Implemented and tested on fakes only: no device with these outputs has run it (`scripts/fan-test.py` is the test for a tester).
- **Sensor offsets (`A79674B4`):** passed to the EC/MCU by the SMF client [C, GUID references], commands not analysed.
- **`CF2F6076`, `349D0E39`:** SoC-specific values for the IHV CPU client and the AMD driver, not analysed.
- **`87008365/220, 250, 300`:** the Surface service (power-meter polling, camera noise reduction), not relevant for limits.

## 4. PL3 and PL4 on the SB3 15" [M, 16.11.2026]
`scripts/pl34-test.py` as root, on AC, platform profile as set, each step with 6 s all-core load, RAPL power in 100 ms samples:

| Step | Package max / mean (W) | psys max / mean (W) | Notes |
|---|---|---|---|
| Firmware: PL4 (powercap `peak_power`) 0, MSR `0x601` 0; PL3 65 W, enabled | 33.8 / 20.2 | 41.8 / 34.7 | baseline |
| PL4 20 W | 12.0 / 10.2 | 23.4 / 21.9 | binds; read-back 20 W, MSR `0x601` = 160 units of 1/8 W |
| PL4 107 W (the blob's value on AC) | 25.7 / 13.2 | 40.1 / 25.5 | written; lower means after the preceding runs [H: heat] |
| PL4 back to 0 | – | – | read-back 0, MSR `0x601` 0 |
| PL3 20 W, enable bit as found | 8.3 / 6.4 | 19.5 / 17.8 | binds, on the platform (psys) power |
| PL3 100 W (the blob's value on AC) | 29.2 / 17.6 | 40.3 / 30.9 | written; time window and enable bit unchanged |
| Restore | – | – | PL4 0, MSR `0x615` identical to the firmware value |

- PL4 reads 0 on this device because the firmware sets none; writing a value sets MSR `0x601`, and writing 0 clears it again.
- PL3 is a platform limit, as its Microsoft name (`O_SYS_PEAK`) says. Windows raises it from the firmware's 65 W to 100 W on AC.
- Both are switched on in the SB3 profile. With them, the SB3 15" writes every output of its blob.
