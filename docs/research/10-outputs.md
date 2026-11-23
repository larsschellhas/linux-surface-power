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
- **`110DF2A4/3`** (`O_MINPWR2`): no Windows client takes it; the power tracker only logs it [C]. Not needed on Linux (`12-remaining-outputs.md`).

On the SB3 15" (engine run, skin sensors 505 and 509 at the same temperature): `O_HOT` is 2 from about 54 °C to 56 °C, `O_CRT` is 2
from about 56 °C up to 100 °C. The daemon's own emergency rule (skin ≥ 60 °C for 10 s) only restores the firmware limits.

On Linux `O_HOT`/`O_CRT` are thermal actions since 16.11.2026: hibernate (else suspend) and power-off, armed in control mode
(`../specs/outputs.md` 7). The throttle requests are written as cpufreq caps per CPU policy and a GPU
frequency cap since 16.11.2026 (`../specs/outputs.md` 8); the ACPI `OPTS` lists in the public dumps show that x86 devices have only the
Processor Aggregator Device (`PAGD`, matched with `/62` by a fixed name table in the driver [C]), so only the Snapdragon (arm64) devices have per-CPU, cluster and GPU requests; untested on a device.

## 3. Others
- **Power limits (`0B7B5350`):** IHV CPU client (Intel, Qualcomm), AMD SMF driver, GPU power controller (Laptop Studio, 201–208),
  battery client (its own channels 300/400, not 70) [C]; 70 "battery mode" makes the SoC client send one EC request on battery (SAM TMP `0x1b`, probably "de-assert PROCHOT" [H]), see `12-remaining-outputs.md`. Linux writes PL1, PL2 and psys PL1/PL2 on Intel through powercap, PL4
  where powercap reports it and PL3 through MSR 0x615 (both held while their EC inputs are invalid), and the dGPU limit through the
  clock-cap stand-in.
- **Fans and charging (`B620798F`):** the SMF client sends each channel as one SAM command to the EC (FAN and TMP categories); the
  commands, units and a safe test procedure are in [`11-fan-charging.md`](11-fan-charging.md). Since 16.11.2026 the daemon sends the
  limits (101–103, 111–116, 121–126, 306, 312) through the SSAM debug device (`../specs/outputs.md` 9); 201 and 301 are sent by the
  linux-surface kernel (`surface_platform_profile`), and the temperature thresholds 302–311 stay off unless a device profile opts in.
  Implemented and tested on fakes only: no device with these outputs has run it (`scripts/fan-test.py` is the test for a tester).
- **Sensor offsets (`A79674B4`):** SAM TMP `0x24` with the offset in 0.1 K, sent by the SMF client; written by the daemon since 16.11.2026 (`12-remaining-outputs.md` 2.3).
- **`CF2F6076`:** channel 61 is the PL1 time window of the IHV CPU client (`12-remaining-outputs.md` 2.1), written through powercap. `349D0E39` is not analysed: it has entries in every Intel client table (targets `0x5b10` and MSR `0x610`/MMIO `0x59a0` bits, depending on the table, `15-mmio-evidence.md` 1) and is used by the Laptop Studio 2 blob (`plan.md` H5); its meaning is [H].
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

### 4.1 How Windows writes PL3, and on which devices [C, 18.11.2026]
Read in the Surface CPU SMF client builds of the driver corpus (x64):
- Channel 59 goes to MSR `0x615` as read-modify-write of **bits 12:0** only, in units of 1/8 W (fixed; MSR `0x606` is not read).
  Enable, clamp, time window and lock bits stay as read. Gemini Lake writes it to MMIO instead (MCHBAR `0x71f0`), every other Intel
  platform type to the MSR.
- No condition on the write: no CPU model, capability, enable- or lock-bit or registry check.
- **Which platform type a device has** is fixed by the Surface integration driver of its package: it creates exactly one CPU client
  child, unconditionally, with a hardware ID naming the type (Ice Lake, Tiger Lake, Alder Lake, Raptor Lake, Meteor Lake, or
  `MSHW0600`). Only the Gemini Lake type selects the MMIO table; no package of the corpus creates it. Go 3 and Go 4 create no such
  child: there the client binds to `ACPI\MSHW0301` and takes its type from an ACPI `_DSM` (UUID `3d011f96-f8d3-499b-8a80-3dd2b09d51ed`,
  revision 0, function 1, integer result; 1–9 accepted) [C, client `0d5efa50`, the build of both packages]. In the public DSDT dumps
  (linux-surface/acpidumps) the `MSHW0301` device returns **1 on the Go 3** (Ice Lake type, MSR table) and **5 on the Go 4** (Alder
  Lake EC type, MSR table) [C, 19.11.2026]. That the firmware on a tester's device returns the same value is [H] (the dumps carry no
  firmware version); a report with `acpidump` confirms it.
- **Verified per device [C, 18.11.2026]** (driver package of the device, client build by sha256 prefix): MSR `0x615` on
  Surface Book 3 13"/15" (Ice Lake, `8030e5ac`), Laptop Go 1 (Ice Lake, `dee1fad5`), Pro 7+ and Pro 8 (Tiger Lake, `18ed0894`),
  Laptop 4 Intel, Laptop Go 2, Laptop Studio 1 (Tiger Lake, `0d5efa50`), Pro 9 Intel, Laptop 5, Laptop Go 3 (Alder Lake,
  `0d5efa50`), Laptop Studio 2 (Raptor Lake, `0d5efa50`), Pro 10 (Meteor Lake, `463d42bc`), Pro 10 5G (Meteor Lake, `3db5fe38`),
  Laptop 6 (Meteor Lake, `0d5efa50`), Laptop 7 5G Intel (Meteor Lake, `f884dca0`), Pro 11 Intel and Laptop 7 Intel (`MSHW0600`,
  `0d5efa50`). Go 3 and Go 4: MSR `0x615` by the `_DSM`
  value in the public dumps (above), no profile switches it on yet. Measured only on the SB3 15" (above) and by a tester on a Pro 9.
- **PL1/PL2 on Windows go to MMIO** (MCHBAR `0x59a0`, PL2 in its upper half; Gemini Lake `0x70a8`), bits 14:0 with enable and clamp set, in
  every table of these builds; the MSR `0x610` copy has its own channels (56/57), which no blob of the corpus uses. Linux writes PL1/PL2 through powercap (MSR). Where
  the firmware's MMIO copy is lower, it caps the effective limit: on a Pro 10 PL1 above 28 W did not take effect (tester, #2383) [M]. Consequences: `../specs/outputs.md` 3.3.
- **Linux:** PL3 is opt-in per profile (`pl3 = true`), only where Windows' write is verified in the driver and a profile
  matches the device: since 18.11.2026 SB3, Pro 8, Pro 9 Intel, Pro 10, Laptop Go 1, Laptop Studio 1/2; since 23.11.2026 also the
  Pro 7+, Pro 10 5G, Pro 11 Intel, Laptop 4 Intel, Laptop 5, Laptop 6, Laptop 7 Intel, Laptop 7 5G, Laptop Go 2/3, Go 3 and Go 4 (profiles by catalogue SKU, `../plan.md` F5). The daemon changes the
  same bits 12:0. It converts with the RAPL power unit from MSR `0x606`; on the SB3 that is 1/8 W, as Windows assumes [M].
