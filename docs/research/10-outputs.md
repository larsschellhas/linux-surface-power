# Outputs of the blobs and what they do on Windows

Phase 7 (16.11.2026). The catalogue of every output in the corpus is in [`../devices/README.md`](../devices/README.md); this file
records what is known about the receivers. Markers as in `README.md`.

## 1. Rule for control [D]
A device is ready for control only when Linux writes every output of its blob. A blob that regulates the CPU while its fan, throttle or
safety outputs go nowhere runs with part of its design missing. By this rule no device is ready yet (16.11.2026); the SB3 15" lacks
PL4, PL3 and the two thermal-policy requests below.

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

Linux candidates [H]: hibernate and an orderly power-off for `O_HOT`/`O_CRT`; cpufreq caps per CPU cluster and a GPU frequency cap for
the throttle requests. Not implemented.

## 3. Others
- **Power limits (`0B7B5350`):** IHV CPU client (Intel, Qualcomm), AMD SMF driver, GPU power controller (Laptop Studio, 201–208),
  battery client (70 battery mode) [C, GUID references]. Linux writes PL1, PL2 and psys PL1/PL2 on Intel through powercap; PL4 and
  PL3 only with a profile switch; the dGPU limit through the clock-cap stand-in.
- **Fans and charging (`B620798F`), sensor offsets (`A79674B4`):** the SMF client passes them to the EC/MCU [C, GUID references]. The
  EC commands are not analysed.
- **`CF2F6076`, `349D0E39`:** SoC-specific values for the IHV CPU client and the AMD driver, not analysed.
- **`87008365/220, 250, 300`:** the Surface service (power-meter polling, camera noise reduction), not relevant for limits.
