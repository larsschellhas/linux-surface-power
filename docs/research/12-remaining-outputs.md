# The four remaining Pro 10 outputs

16.11.2026. After `11-fan-charging.md` the Pro 10 wrote 13 of 17 outputs. This note records what Windows does with the other four, the
Linux path chosen for each, and the checklist for a Pro 10 tester. Markers as in `README.md`. Nothing here ran on a device. Evidence:
the Pro 10 driver package (SMF client, SoC CPU client, thermal policy, SMF core, power tracker), the same drivers of the Pro 9,
Laptop Studio 2, Laptop 7 Snapdragon and Laptop 4 AMD packages, and the engine replay of the 41 blobs of the corpus.

| Output | Value in the Pro 10 blob | Windows receiver | Linux |
|---|---|---|---|
| `CF2F6076/61` | 1000, 8000, 28000 or 30000 (ms) | SoC CPU client: PL1 time window [C] | written: powercap `package-0/long_term` time window |
| `A79674B4/509` | constant 0 (0.1 K) | SMF client: virtual-temperature offset of sensor 9, SAM TMP `0x24` [C] | written: same SAM request through the debug device |
| `0B7B5350/70` | 1 while the adapter rating is 0, else 0 | SoC CPU client: no register write; value 1 sends one EC request (SAM TMP `0x1b`, ACPI-EC machines: method `DPHT` = de-assert PROCHOT [H for the link]) [C] | written: same SAM request once per episode (Intel, SAM machines); otherwise no path |
| `110DF2A4/3` | bit mask 0-7 | no client takes it; only the power tracker records it (telemetry) [C] | not needed on Linux |

## 1. How the blob makes the values [C, engine and block parameters]
- **`CF2F6076/61`** is a lookup of the power slider: state 2 gives 8000, states 3-6 give 28000, state 7 gives 1000, all others (1, 8-10)
  give 30000. The unit is milliseconds [C: see 2.1]. The value does not depend on anything else (no temperatures, no EC data).
- **`A79674B4/509`** is the clamped sum of two lookup tables (posture, adapter rating) whose outputs are all 0: the value is 0 in every
  input state, also with every input invalid [C]. Other blobs have non-zero values: -3.0 K to +2.5 K as table sums (Pro 7+, Laptop 4 Intel, Laptop 5/6/7,
  Pro 11 Intel, Laptop Go 1), in 0.1 K (corrected 27.11.2026, section 2.5).
- **`0B7B5350/70`** is a lookup of the adapter rating (`I_PSU_SIZE`): 1 when it is exactly 0, else 0 (an invalid rating gives 0). So it
  follows the adapter: 1 on battery, 0 with a supply that reports a rating; no temperature, charge or standby input. The Snapdragon,
  Pro 12 and Laptop 13" blobs use the available input power (`I_IPL_TOP`) instead: 1 when it is 0 or above 200 W.
- **`110DF2A4/3`** adds three flags: the outputs of three controllers (skin sensor 509, battery discharge rate, one more) divided by 15 W,
  each flag set when the result is 0-75 %, with weights 1, 2 and 4. So it is 0-7: "which controller is below 11.25 W".
  Other blobs reach 0-7 as well.

## 2. What the Windows receivers do

### 2.1 `CF2F6076/61`: PL1 time window [C code and table, H for the unit]
- The Intel SoC client has a table of target descriptors per channel: register (MMIO or MSR), bit field, shift. Channel 61 is a 7-bit
  field at bit 17 of the register that also holds PL1 (MMIO `0x59A0` on one table, `0x70A8` on another; channel 62 is the same field in
  MSR `0x610`). Bits 23:17 of the package power-limit register are the time window (5-bit exponent, 2-bit fraction) [SDM].
- Before writing, the client rounds the value **up** with a table of 28 thresholds, 1024 to 114688, four per octave [C: table and
  lookup read in the Pro 10 client]. Each entry is a pair (threshold, 7-bit code) and the code equals Intel's encoding Y (bits 4:0) and Z
  (bits 6:5), time = 2^Y x (1 + Z/4) x time unit [SDM]; every code checked against its threshold. The client compares the
  millisecond value directly with the unit counts and never reads the time unit (MSR 0x606 bits 19:16), so it assumes 1 unit = 1 ms
  nominal. With the register's default unit 1/1024 s [H: not read on the Pro 10] the windows are 1.0 s to 112 s: 1000 gives 1.0 s,
  8000 gives 8.0 s, 28000 gives 28.0 s, 30000 gives 32.0 s. A value above the table gives code 0. Whether Windows writes the field on
  every change or only once is not shown by the client; the blob changes the value only with the slider state [H].
- The power tracker names the channel "average time" (`O_CPU_AVG_TIME`, `O_CPU_AVG_TIME2` for 62) [C, name tables of the Pro 9 and
  Laptop Studio 2 packages]. Same descriptors in the Laptop Studio 2 client.
- **Linux:** powercap `intel-rapl:0` (`package-0`) `constraint_0_time_window_us`, the `long_term` constraint that holds PL1. The value
  in ms is compared with the unit counts like the Windows client does (so 1001-1024 ms give 1.0 s), becomes µs, is clamped to 1.0-112 s
  and rounded up to the same 28 steps. The kernel then encodes the field itself and rounds **down** to its own 4 steps per octave
  with its time unit (976 µs for the default 1/1024 s) [C: `intel_rapl_common.c`]; the steps above are chosen so that this gives the
  same code as Windows. The old time window is in
  the snapshot (it was already, as the companion of the PL1 file) and is restored on exit. The read-back of a window can differ by
  up to ~14 % (4 steps per octave), so the foreign-change check allows 15 %.

### 2.2 `0B7B5350/70`: battery mode [C for the request, H for its meaning]
- The earlier reading "only a request flag to the SMF core" was wrong. The SoC client's write handler (all Intel plugin variants) sets a
  one-byte result when the value is exactly 1 (value 2 in the Qualcomm plugin). The client's own batch handler acts on that byte at
  once: it is not passed on to the SMF core, which only keeps the interface GUID in a list and has no logic for it [C].
- **Effect:** one EC request per delivery of the value 1, nothing for 0 (no "off" request) [C].
  - SAM machines: SAM request target category 3 (TMP), target ID 1, instance 0, flags 0, command `0x1b`, no payload (the Pro 8 client of
    2021 sends the same). Qualcomm plugin: command `0x1e` with a 4-byte payload `0x00001000`.
  - ACPI-EC machines (Go 3, Go 4; selected by a client setting): ACPI method `DPHT`. In the DSDT of these machines `DPHT` sends EC
    command `0x36`, which the table itself labels "de-assert PROCHOT" [C]. The SAM command sits at the same place of the same code
    with the same condition, so TMP `0x1b` is probably its SAM form [H]; it is not measured. The Pro 10 tables have no such method.
  - What the EC does with it (clear a PROCHOT that was set while the adapter was connected) is [H]; the EC asserts it again if the cause stays.
- Not an effect (searched in all Intel client builds): no write of the energy-performance bias (MSR `0x1B0`) or HWP request (`0x774`),
  no DTT or PPM mode, no change of the client's own power-limit tables. Its register table holds only MSR `0x601`, `0x610`, `0x615`,
  `0x65c` and four MMIO offsets (channels 51-59); channels 70 and 71 have no register [C].
- Other receivers: the battery client registers 250/300/350/400 (`O_BPM_DISABLE`), the GPU power controller 201, 202, 205, 208, 210, the VPU
  client channel 64, the power tracker only records `O_BATT_MODE` [C].
- **Linux:** `adapters/prochot.py` sends the same SAM request through the SSAM debug device once when the value turns 1 (also at the
  first cycle and after a resume), dry-run by default, retry after 5 s, log-only after three failures. Nothing is restored: the request
  releases a state and sets no level. No path (log-only, blocks readiness): ACPI-EC machines (the ACPI method cannot be called from user
  space), and AMD or Qualcomm CPUs (those clients are not analysed; the AMD packages of the corpus hold no CPU client at all).

### 2.3 `A79674B4/509`: virtual-temperature offset of sensor 9 [C for the frame, H for path and effect]
- The SMF client takes the 12 channels 501-512 of this interface. For each updated one it sends a SAM request: target category 3
  (TMP), instance = sensor number, flags 0 (write), command `0x24`, payload 5 bytes: the sensor number as u8, then the value x 0.1
  as float32 (K) [C]. On EC (ACPI) platforms the same value goes to an ACPI method instead; the Pro 10 has
  SAM temperature sensors, so the SAM path applies [H]. Sensor 9 is the "virtual" skin sensor (`I_VTS1`, hwmon `temp9`).
- On the Pro 10 the request carries 0.0 K every time. The module sends a channel when the core marks it updated; that this happens
  once at start and after a resume (as for the fan outputs) is [H]. Frame layout (category 3, target ID 1, instance = sensor number
  1-12 for channels 501-512, command `0x24`, length 5, sensor byte, float32 = value x 0.1) read from the SMF client of the Pro 10 [C].
  The client has a second path (an ACPI method) that it takes only for one platform type; the Pro 10 uses the group of the Pro 9 in the
  kernel registry with SAM temperature sensors [C], so the SAM path is the likely one [H]. The unit of the float (K) and the effect on the EC are [H].
- **Linux:** the same request through `adapters/fan.py` (spec outputs 9): same dead band, rate limit, clamp (hard bound +-5 K, the
  blob has no range for it), neutral frame = offset 0, recorded in the snapshot before the first send and sent at restore.
  Effect on the EC: not measured; 0 is what Windows sends [H: it equals the firmware default].

### 2.4 `110DF2A4/3`: O_MINPWR2 [C, search list below]
- **What drives it:** three flags from controller outputs (skin temperature, battery discharge rate, one more), so it changes with
  heat, discharge and slider, not with the adapter [C blob].
- **Searched:** the interface GUID (little-endian, ASCII, UTF-16, and its first four bytes alone) in all 127 distinct driver, service and
  DLL binaries of the corpus (215 files, 40 device packages, x64 and arm64). Hits: SMF core (list of interface GUIDs, no logic),
  power tracker (name table), `SurfaceService.exe` and `SurfaceThermalService.exe` (GUID lists without channel numbers), thermal policy. None in
  the SMF client, display client, sensor client, CPU client, battery, charger, hot-plug and integration drivers.
- **Thermal policy:** every build decoded registers channels 1 and 2 of this interface (newer builds: a loop 1..2; older builds such as
  Go 3, Laptop Go 2: the first two outputs not matched to a zone name) and one more on `9BBB8910`; none takes channel 3 [C]. (18 distinct thermal-policy builds of the corpus; the blobs that have channel 3 all belong to the newer builds.)
  The zone policy structure it fills has a hibernate flag (`O_HOT`), a critical flag (`O_CRT`) and a passive limit; its other fields
  (standby, activation reasons, active level) are never written, so there is no field channel 3 could map to [C].
- **Pro 10 ACPI:** `TPOL` has only `_STA` and `OPTS`; no method takes a battery-mode or minimum-power argument [C].
- **Power tracker:** lists it as `O_MINPWR2` among the items it records into its ETW events; no hardware access [C].
- **Linux:** not needed: the value is recorded by Windows telemetry only. A reader of those ETW events outside the corpus cannot
  steer hardware.

### 2.5 `A79674B4/509` on the Laptop Go 1, 2 and 3: clients, blobs and EC firmware (27.11.2026)
Evidence: the SMF clients (sha256 Go 1 `68f6cffd…`, Go 2 `0a7fb974…`, Go 3 `1973365a…`) and the SAM firmware of the same driver
packages (Go 1 7.100.139, Go 2 6.302.139, Go 3 12.202.139; unpacked with `linux-surface/sam-fwxtract`, read statically). Nothing ran
on a device.
- **Clients [C]:** all three build TMP `0x24` as in 2.3 (instance = sensor number, sensor again as first payload byte, flags 0).
  The byte positions agree with the trip-point request `0x09` of the same Go 1 client.
- **EC dispatch [C]:** requests are handed out by target category only; every TMP request goes through the product handler first.
  The layers below that queue were not read.
- **Go 1 and Go 2, `0x24` [C]:** the sensor is payload byte 0; the instance field is not read. Only sensors 9-11 of the virtual type.
  New constant of the virtual sensor = float from the request + factory constant: not cumulative, 0 = factory value. No range or NaN
  check. On the Go 1, `VTS = constant + weighted sum of RTS sensors` in °C, so the float is in K. The Go 1 firmware reloads the table
  constants when it initialises or switches the configuration of a virtual sensor, which drops the host's offset.
  Go 1: the table pointer the handler reads the factory constant from is set for sensor 9 only and is not checked [C]; what a request
  for sensor 10 or 11 does there is [H].
- **Go 3 [C]:** none of its three TMP handlers compares the command with `0x24`: no handler. Its blob only sends 0.
- **Sensors and fan [C]:** all three images have `I_RTS1`-`I_RTS8`, `I_VTS1`-`I_VTS3` and the sensor and fan-speed requests the kernel
  drivers use. FAN `0x0e` (profile): no handler on Go 1 and Go 2, handled on the Go 3; FAN `0x0b`: not on the Go 1, handled on Go 2
  and 3. This matches the fan outputs of the three blobs.
- **Blobs [C, engine over all input values]:** 22 of the 41 blobs have channel 509, none another channel. Go 1: -2.0 K (the core's
  rounding gives -19) with an external display, else 0. Go 2 and Go 3: constant 0. Corpus range of the table sums: -3.0 K (Pro 7+)
  to +2.5 K (Laptop 6, Laptop 7 Intel, Pro 11 Intel).
- **Not established:** the effect of our frame on any device. A Go 1 tester measures an effect with instance 0 and none with
  instance = sensor [M, one tester]; the firmware read here does not explain the second observation. The firmware of other devices:
  section 2.6.

### 2.6 `A79674B4/509`: SAM firmware of the other devices (27.11.2026)
Question: which devices handle TMP `0x24`, and what does the handler do with it? **Evidence:** the `SurfaceSAM_*.bin` capsule of each
driver package below, unpacked with `linux-surface/sam-fwxtract`, decompiled with Ghidra 12.2; every branch and every missing branch
checked in the machine code. All ten images are unencrypted, their two slots are byte-identical. Load address 0x80000 (images of
512 KiB: every sensor name pointer resolves) or 0x10080000 (images of 628 KiB: function pointers hit prologues; as on the Go 3). The
sensor tables of the newer images were read from the initialised RAM, rebuilt from the image's own init table (LZ77; each unpacked
block ends at or up to 5 bytes before the following zeroed range). Static only; nothing ran on a device. Files and `SHA256SUMS`:
`raw/tools/fw/decomp/ec-tmp24-2026-11-27/`, local only.

| Device (package version) | SAM version | sha256 of `SurfaceSAM_*.bin` | TMP `0x24` | Blob writes (K) |
|---|---|---|---|---|
| Pro 7+ (26.074.41214.0) | 14.401.139 | `b50b1595d11515819de0cfb69f039944f2fcaaa35427d7e9e46d2ffe7d4cf7ee` | kind A, sensors 9-12; **10-12 broken** [C] | 0 … −2.9 |
| Laptop 4 Intel (26.091.11858.0) | 8.403.139 | `32c8334bbc56ee2b861bcb1799aac5367f9b6121bbf2661c9771e350a2eb4b1a` | kind A, sensors 9-11 [C] | 0, +1.0 |
| Laptop 5 (26.101.10907.0) | 15.503.139 | `fdae13c1af045e477a6ba6ecd22b94794165bd8bb6ef1843f237f65cc0646e14` | **no handler** [C] | 0, +1.0 |
| Laptop 6 (26.093.32457.0) | 10.401.139 | `4ee2bc00015b52641427f2231f1acd16ef99cc5ac144c307ee4d8e39659093da` | kind C, hardware 1-5, virtual 9-11 [C] | 0, +1.0, +1.5, +2.5 |
| Laptop 7 Intel (26.081.15509.0) | 11.103.139 | `eb1ef3d06e18dc4f11257e3616c2db0c0a676e17ce9884e1f791d15b79efe8d2` | kind C, hardware 1-5, virtual 9-11 [C] | 0, +1.0, +1.5, +2.5 |
| Laptop 7 5G Intel (26.074.43712.0) | 5.108.139 | `97b6a4bc2e13c2d47ab077b776b62129e46c546a3263520c0ebcd05af7d0701e` | kind C, hardware 1-7, virtual 9-12 [C] | 0, +1.0, +1.5, +2.5 |
| Pro 11 Intel (26.093.32557.0) | 10.501.139 | `2957fe95658b44c245512662a799c1bcb3f8394310eeeec9021bebb0c0abdaca` | kind C, hardware 1-5, virtual 9-11 [C] | 0, +1.0, +1.5, +2.5 |
| Pro 8 (26.073.32221.0) | 12.100.139 | `f5afc6038d250bb69ae41638099ea5be73de3dfe3bee3cf22966094a51c8c58f` | kind A, sensors 9-11 [C] | 0 |
| Pro 9 Intel (26.091.15297.0) | 6.603.139 | `90c1a6a8aed312ee07e85efbc24da5a6aa9206ed808528be80b28b66358f7167` | **no handler** [C] | 0 |
| Pro 10 (26.091.15004.0) | 29.401.139 | `dd7d77814086e807b28dc9116731a0925c417044d49ae50461ff318c09f9e771` | kind C, hardware 1-5, virtual 9-11 [C] | 0 |
| For comparison (2.5): Go 1 | 7.100.139 | `994a993a48d854b9323eff1dbd1c1f9d5ecb78966bf14af4f1b508288ad2eada` | kind A, sensors 9-11; 10, 11 broken [C pointer, H effect] | 0, −1.9 |
| Go 2 | 6.302.139 | `e009ea244bcd7b39ad490542e139016379aed4517cae7f098465c41c423ef5f8` | kind A, sensors 9-11 [C] | 0 |
| Go 3 | 12.202.139 | `b5f6b56815299bd6e41730197f76f48023f4d2551d359578b157f0b10ffe5c84` | no handler [C] | 0 |

**In every handler [C]:** the sensor is payload byte 0; the instance field of the header is not read; the float (payload bytes 1-4) is
not compared with any limit.

**Kind A, the offset changes the constant of a virtual sensor** (Pro 7+, Laptop 4 Intel, Pro 8; as Go 1 and 2) [C]:
- The first of the TMP handlers takes the command. Only sensors of the virtual type.
- New constant = float of the request (one `vadd.f32`) + factory constant, so not cumulative. The virtual sensor is computed as
  `10 x constant + sum of w x (T - 2731) + 2731` in 0.1 K (plus optional fan terms), so the float is in K.
- **0 restores the factory value** for sensor 9: in every product variant of the three images the table entry that the handler reads
  equals the sensor's own constant. Laptop 4 and Pro 8, sensors 10 and 11: no table pointer, the handler checks that and reads the
  product's default block, the same block a change of product variant gives the sensor (equal to the built-in constants in the
  first variant).
- **Pro 7+, sensors 10-12:** the handler reads the factory constant through the table pointer without checking it. The pointer is
  null for these sensors at start and after the function that sets it (it sets sensor 9 only), and their configuration index is 0, so
  the "constant" is read from address 0x4. What is there is not part of the image [H]; it is not the sensor's constant. A request for
  10-12 would replace their constant with that value plus the float.
- **Lost** when the EC loads the constants again: initialisation (EC restart), TMP `0x0c` (reselects the product variant), TMP `0x8e`
  (selects a sensor configuration), and the automatic change of configuration when a source sensor becomes invalid. TMP `0x03`
  (platform profile, which the Linux kernel sends) is not among the callers of these loads [C for direct calls; calls through
  pointers were not traced].

**Kind B, no handler** (Laptop 5, Pro 9; as Go 3) [C]:
- The three TMP handlers are the same 762 instructions in both images apart from addresses. They have no jump table and no
  comparison or range check on the command number that covers `0x24`; the only range checks are on payload values.
- Second, independent sign: the temperature reads (TMP `0x01`, `0x1d`) return the stored values with no offset term, where kind C
  adds one.
- So the +1.0 K that the Laptop 5 blob writes does nothing on this firmware version, under Windows too.

**Kind C, a separate offset field** (Laptop 6, Laptop 7 Intel and 5G, Pro 10, Pro 11 Intel) [C]:
- The generic TMP handler (service "ThermalSS") takes the command; the two handlers in front of it do not.
- **Whole kelvin only:** `vcvt.s32.f32` (rounds toward zero) comes before the multiplication by 10, and 16 bits are stored. So +1.5 K
  acts as +1.0 K and +2.5 K as +2.0 K (values these blobs write; Windows' own request is affected in the same way), and -2.9 K would
  act as -2.0 K. No range check; large values are cut to 16 bits.
- **Not limited to virtual sensors:** the number is looked up first among the hardware sensors (1-5; Laptop 7 5G: 1-7), then among
  the virtual ones (9-11; 5G: 9-12). Other numbers only log an error. An offset on a hardware sensor shifts every thermistor
  `I_RTSx` fed by it and every virtual sensor, because all of them are computed from the hardware values. Feeds on the Pro 10,
  Pro 11 and Laptop 7 Intel: 1 → RTS1; 2 → RTS2, RTS7; 3 → RTS3; 4 → RTS4, RTS8 (`I_RTS_CRIT` on the Laptop 7); 5 → RTS5, RTS6.
  Laptop 6: as before, except RTS8 comes from sensor 1. Laptop 7 5G: 1-7 → RTS1-RTS7, 4 also `I_RTS_CRIT`.
- The offset is a signed 16-bit field (0.1 K) that is added when a value is read. It lies in memory that is zeroed at start (the
  hardware sensors' field is also written 0 at initialisation), so **0 is the factory state**.
- **Lost** at an EC restart. In the sensor module only the initialisation and this request store into the fields; initialisation
  refuses to run twice, and a change of sensor configuration does not touch them. That no other code clears a whole sensor object is
  [H].
- The answers to TMP `0x01` and `0x1d` contain the offset, so Linux temperature readings would move with it. Which protection
  functions of the EC use the shifted values was not traced [H].

**Re-checked (27.11.2026, kernel#198 review session):** own extraction from the MSI packages (same `SurfaceSAM_*.bin` sha256) and own
decompile for the Pro 9, Pro 10, Pro 11 Intel, Laptop 5 and Laptop 6, checked on the full instruction listing. Confirmed: no handler on
the Pro 9 and Laptop 5 (no compare, jump table, code gap or forwarding of the command id in the three TMP handlers); kind C on the other
three (handler and its helpers identical apart from one log line number; sensor from the payload; rounding toward zero before x 10; no
range check; hardware sensors 1-5 and virtual 9-11; the offset is part of the readings and of the values `I_RTSx` and the virtual
sensors take over). The RAM tables were unpacked with this section's `iardata.py` and match the pointers in the own decompile. The Go 3
result of 2.5 was repeated on the whole image. Kind A rows other than Go 1 and 2 were not re-checked.

**Not read:** Surface Book 3 (the package is not stored locally; its two blobs do not have channel 509), Pro 10 5G and every other
package. **For all rows:** the finding holds for the firmware version of the package; a device may run another one [H]. The layers
below the TMP message queue were not read [H]. No effect of our frame is measured on any device.

**What a kernel attribute `temp9_offset` could claim** (sensor 9 is `temp9` of `surface_temp`):

| Finding on sensor 9 | Devices |
|---|---|
| handled, 0 = factory value [C, static] | Go 1, Go 2, Pro 7+, Pro 8, Laptop 4 Intel; with whole-kelvin steps: Pro 10, Pro 11 Intel, Laptop 6, Laptop 7 Intel, Laptop 7 5G Intel |
| no handler, so no attribute | Go 3, Laptop 5, Pro 9 Intel |
| not read | Surface Book 3 and all others |

- **No device is proven completely** in the sense of the project rule (`CLAUDE.md`, "Kernel changes"): no reading from a device
  shows the effect, and the SAM version of no tester device has been compared with the package. On kinds A and C `temp9_input`
  should move with the offset, so one read before and after a write would show it.
- **Who would use it:** devices whose blob writes something other than 0: Go 1, Pro 7+, Laptop 4 Intel, Laptop 6, Laptop 7 Intel and
  5G, Pro 11 Intel (Laptop 5 too, but its firmware has no handler). Pro 8, Pro 10 and Go 2 only ever write 0.
- **Registry** (`v7.3-surface-devel` at `96cd24ea`) [C]: a property per sensor group cannot separate `sp9_dptf`, which serves the
  Pro 9 (`MSHW0343`, no handler), Pro 10 (`MSHW0510`) and Pro 11 (`MSHW0583`), or `slg1` (Go 1, 2 and 3); the group must be split.
  `sl3_dptf` serves the Laptop 4 Intel (`MSHW0250`) and AMD models (`MSHW0110`, firmware not read). `sp7_dptf` (Pro 7+) and `sp8`
  serve one ID each. A TMP sensor node exists for the Pro 9/10/11 group, Laptop 5 and Laptop 6; it is missing for the Go models,
  Pro 7+, Pro 8, Laptop 4 and Laptop 7 (`plan.md` D9-D11). Which registry entry the Laptop 7 5G Intel uses is [H].
- **What the driver would have to do:** offer the attribute for sensor 9 only (kind C would accept hardware sensors, the Pro 7+
  firmware breaks 10-12); bound the value itself (the EC does not; the blobs stay within -3.0 … +2.5 K); say that on kind C only
  whole kelvin act; keep the last value itself (no request that reads the offset back is known) and expect that on kind A the EC
  can drop it without telling the host.

## 3. Other devices
`scripts/device-docs.py` shows, per device page, which of the four channels the blob has:
- `CF2F6076/61` and `0B7B5350/70`: most Intel devices of 2020-2024 (Pro 7+, 8, 9, 10, Laptop 4 Intel/AMD, 5, 6, Go 1-3, Go 3/4,
  Studio 1/2); time window values are always from {1000, 8000, 28000, 30000}.
- `A79674B4/509` (table sums; corrected 27.11.2026): Pro 7+ (0 to -30), Laptop Go 1 (-20, 0), Laptop 4 Intel (0, 10), Laptop 5 (0, 10),
  Laptop 6, Laptop 7 Intel and Pro 11 Intel (0, 10, 15, 25), the others constant 0. Generic: every device with the SAM thermal sensors gets the same request;
  what the EC does with it differs by firmware (2.5, 2.6).
- `110DF2A4/3`: 0-7 on newer devices, also arm64.
- The same rules apply: AMD and Snapdragon devices have no powercap, so `CF2F6076/61` stays without a path there; `70` has none either
  (Intel SAM machines only; Go 3 and Go 4 are ACPI-EC machines).
- With these rules 9 more Intel devices cover every output (device pages); all stay dry-run by default (no profile, or `tested = false`).

## 4. Pro 10 tester checklist (technosf)
The profile `surface-pro-10.toml` has `tested = false`: the daemon never controls unless asked. Everything runs as root only where
stated. No other power tool (thermald, tuned, TLP) may run. AC power, battery above 50 %, nothing else heavy running.
1. **Facts:** `surface-power-report` (shows blob, inputs, and per output "writable (...)" / "not needed on Linux: ..."; `ready for control`
   must be `yes`). Send its text with the results.
2. **Probe, nothing written:** `sudo python3 -I scripts/fan-test.py OUT --probe` (reads the fan rpm and system SPL).
3. **Dry run of the daemon:** `surface-powerd --dry-run` for 10 minutes; look at the status file `/run/surface-power/status.json`: every
   output has a target, `fan` and `outputs` show `would send ...`, nothing written. Check `journalctl` for warnings.
4. **Single-channel tests**, each with its own script, one at a time (every script restores and logs; run `--dry-run` first):
   `scripts/fan-test.py OUT --channel 201` then `121`; `scripts/throttle-test.py` (the `/62` cap); `scripts/pl34-test.py` (PL4 and PL3).
5. **Daemon in control mode,** short and watched: `sudo surface-powerd --control` for 5 minutes at idle, then with a CPU load. Watch
   `sensors` (skin, CPU), `fan1_input`, `cat /sys/class/powercap/intel-rapl:0/constraint_0_*`. Expected in the status file: PL1/PL2/psys
   written, the PL1 window 28 s (slider 3-6) or 1/8/32 s, `A79674B4/509` sent once with 0.0, `0B7B5350/70` "SAM TMP 0x1b (cdev)" (one request while on battery, check the journal line), `110DF2A4/3` "not needed".
   Stop with Ctrl-C or `systemctl stop`: the powercap values, the window, the cpufreq caps and the neutral fan and offset frames are restored
   (log line "firmware values restored"); if the process was killed, run `surface-power-reset`. Last resort: reboot.
6. **Report:** the report text, `status.json` from step 5, the journal, and whether the fan or temperatures behaved differently from the
   dry run. The effect of the TMP `0x24` request and of the fan frames on the EC is unknown: say if the status shows request failures.
- **Known gap:** `I_CPU_TEMP_AVG` is the package temperature as the Windows client reads it, one sample without averaging [C] (DV-7) and `I_AMBIENT_AI` is not provided (reaches no output in any blob that reads it, `09-newer-blobs.md` section 6).

## 5. Open points
- Whether the register's time unit is 1/1024 s on the Pro 10 (default MSR `0x606` bits 19:16 = 10) [H]; Linux rounds with the real unit.
- Whether TMP `0x1b` really is the EC's de-assert-PROCHOT command and what the EC does with it on battery [H]; also whether the core delivers the value on change only or with every batch (Linux sends once per episode).
- The effect of `TMP 0x24` on a device. In the firmware of the driver packages, offset 0.0 for sensor 9 equals the power-on state
  wherever a handler exists, the Pro 10 included [C, static, sections 2.5 and 2.6]; the Go 3, Laptop 5 and Pro 9 firmware has none.
- The Pro 10 battery driver binary was not available; the Pro 9 and Laptop 7 ones do not register channel 70.
- Provenance of the decompiles: `raw/tools/fw/decomp/p10c/` (Ghidra 12.2, `DecompEach.java`), local only.
