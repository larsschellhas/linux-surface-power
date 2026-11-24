# Evidence for the MMIO write path of PL1/PL2 (per platform type)

23.11.2026, updated 24.11.2026 (SB3 module tests). Basis of `specs/kernel-mmio-rapl.md`. Markers as in `README.md`: [C] driver code or kernel source, [M] measured, [H] hypothesis.
Nothing here names Windows functions or reproduces code; it states behaviour, registers and parameters.

## 1. What the Windows CPU client writes [C]
The Surface CPU SMF client (`SurfaceIhvCpuSmfClient.sys`) has one descriptor table per platform type. Read for seven distinct Intel builds by
parsing the descriptor tables (23.11.2026): `8030e5ac` (Book 3), `dee1fad5` (Laptop Go 1), `18ed0894` (Pro 7+, Pro 8), `0d5efa50` (most 2021–2025
devices), `463d42bc` (Pro 10), `3db5fe38` (Pro 10 5G), `f884dca0` (Laptop 7 5G). In **every** build:

| Channel | Meaning | Target | Mask | OR-ed bits |
|---|---|---|---|---|
| 51 | PL1, package long-term | MMIO `MCHBAR + 0x59a0` (bits 14:0) | `0x7fff` | `0x18000` = enable (bit 15) and clamp (bit 16) |
| 52 | PL2, package short-term | MMIO `MCHBAR + 0x59a4`, or `0x59a0` shifted by 32 in two tables of `0d5efa50` and `f884dca0` (same bits) | `0x7fff` | `0x18000` |
| 61 (`CF2F6076`) | PL1 time window | MMIO `0x59a0`, bits 23:17 | `0x7f` | none |
| 53 | PL4 | MSR `0x601`, bits 12:0 | `0x1fff` | none |
| 54, 55 | psys PL1, PL2 | MSR `0x65c` | `0x7fff` | `0x18000` |
| 56, 57 | PL1, PL2 in the MSR copy `0x610` | MSR | `0x7fff` | `0x18000`; **no blob of the corpus has these channels** (a statement about the blobs, grep over `smf-blob/geraete`, not about the driver) |
| 62 (`CF2F6076`) | window of the MSR copy | MSR `0x610`, bits 23:17 | `0x7f` | none; not used by any blob |
| `349D0E39/3` | – | MSR `0x610` bit 16 | – | not used by any blob; `349D0E39/1, /2` target `0x5b10`, `/4` MMIO `0x59a0` bit 16 or `0x5b10` bit 31 depending on the table (used by the Laptop Studio 2 blob: the table of that device must be resolved before it enters the module's table); further `0B7B5350/60, /61` and `CF2F6076/65` target `0x5e90`. `research/10-outputs.md` lists `349D0E39` as AMD and not analysed: it is present in every Intel table |
| 59 | PL3 | MSR `0x615`, bits 12:0 (Gemini Lake: MMIO `0x71f0`) | `0x1fff` | none |

- The Gemini Lake table (PL1/PL2 at `0x70a8`) exists in every build but no package of the corpus creates that platform type.
- Written value = (mW × scale / 1000 & mask) | OR-bits, read-modify-write of the target. The access width of the MMIO write is not established (dword at `0x59a4`
  in most tables, a shifted qword in two) [H]; the kernel's MMIO zone uses 64-bit reads and writes of `0x59a0` [C].
- **Windows leaves the MSR copy `0x610` alone** as far as the SMF client goes (channels 56/57 unused). Other Windows components writing it: not checked [H].

## 2. MCHBAR per platform type [C, Windows constant; M for four devices]
| Type | Hardware ID | Table | MCHBAR the client assumes | Devices (client build) |
|---|---|---|---|---|
| 1 Ice Lake | `SurfaceSmfIcelakeClient` | A | `0xfed10000` | Book 3 13"/15" (`8030e5ac`), Laptop Go 1 (`dee1fad5`) |
| 2 Tiger Lake | `…TigerlakeClient` | A | `0xfedc0000` | Pro 7+, Pro 8 (`18ed0894`), Laptop 4 Intel, Laptop Go 2, Laptop Studio 1 (`0d5efa50`) |
| 4 Alder Lake | `…AlderlakeClient` | C | `0xfedc0000` | Pro 9 Intel, Laptop 5, Laptop Go 3 (`0d5efa50`) |
| 5 Alder Lake (EC) | `…AlderlakeClientEc` | C | `0xfedc0000` | Go 4 (type from an ACPI `_DSM`, 5 in the public dump [H firmware]) |
| 6 Raptor Lake | `…RaptorlakeClient` | D | `0xfedc0000` | Laptop Studio 2 (`0d5efa50`) |
| 7 Meteor Lake | `…MeteorlakeClient` | E | `0xfedc0000` | Pro 10 (`463d42bc`), Pro 10 5G (`3db5fe38`), Laptop 6 (`0d5efa50`), Laptop 7 5G (`f884dca0`) |
| 8 `MSHW0600` | – | F | `0xfedc0000` | Pro 11 Intel, Laptop 7 Intel (`0d5efa50`) |
| Go 3 | – | via ACPI `_DSM` | type 1 in the public dump [H firmware] | Go 3 |
The assignment of device to type comes from the integration driver of each package (`pl3-treiberbeleg`, earlier work); only the Pro 10 table (E) was re-read on 23.11.2026. **Verified on 23.11.2026:** both constants occur once in every client binary. **Not re-verified:** which constant belongs to which type, and the device-to-type mapping (taken from the earlier notes); the names "Alder Lake (EC)", Raptor Lake and Lunar Lake are the notes' labels. The module's MCHBAR check can only tell Ice Lake from the others.
Both constants appear once in every client binary. Tables for types 3 (Gemini Lake) are not used.
- **Linux reads the real MCHBAR** from the host bridge 00:00.0, config `0x48`/`0x4c` (bit 0 enable, address bits 41:15). Compared with the client's constant:
  Surface Book 3 15" `0xfed10000` [M, 18.11.2026, script `mmio-pl-read.py`]; Pro 10 `0xfedc0000` (config `0xfedc0001`) [M, tester, 18.11.]; Pro 9 `0xfedc0000` [M, tester]; Pro 8: the tester read the MMIO copy with the script, which reads the address from the same config register (it has no constant); the values are plausible [M, tester]. For the other types the match is [H].

## 3. What Linux has [C, kernel source `v7.3-surface-devel` and 7.3.4-2-surface headers, 23.11.2026]
- `processor_thermal_rapl.c` creates the zone `intel-rapl-mmio` from `mmio_base` of the processor thermal PCI device (00:04.0): registers `0x59a0` limit, `0x5938` unit, `0x593c`
  energy, `0x58f0` perf status, `0x5930` info, `0x59b0` PL4 (and DRAM `0x58e0…`). Bits: PL1 14:0, enable 15, clamp 16, window 23:17, lock 31; PL2 the same +32. This is the Windows address and bit layout.
- PCI IDs with the RAPL feature: Skylake, Ice Lake, Tiger Lake (legacy driver); Alder Lake, Raptor Lake, Meteor Lake, Lunar Lake and later (new driver). So the kernel already covers the generation of
  every x64 SMF device **when the firmware shows 00:04.0**.
- 00:04.0 is absent from the PCI bus on: Surface Book 3 15" [M], Pro 8 [M, tester], Pro 9 Intel [M, tester], Pro 10 [M, tester]; present on a Laptop 3 (linux-surface#1917) [M]. Why the firmware hides it is unknown [H].
- The public API for an out-of-tree zone is `include/linux/intel_rapl.h` (`rapl_add_package`, `rapl_remove_package`, `rapl_default_*`, exported in the namespace `INTEL_RAPL`); the private
  `proc_thermal_rapl_add()` needs a header that is not installed. `read_raw`/`write_raw` signatures and the register enum changed between kernel versions (a `RAPL_DOMAIN_REG_PL2` entry exists in 7.3.4); any
  build is therefore tied to a kernel version.
- Powercap writes go through `write_raw` with a mask. The zone attribute `enabled` is not a single bit: it sets PL1 enable, PL1 clamp, PL2 enable and PL2 clamp, and the core clears enable and clamp of every limit when a package is removed [C, `intel_rapl_common.c` `set_domain_enable`, `rapl_default_set_floor_freq`, `rapl_remove_package_cpuslocked`, verified 23.11.2026].

## 4. Measured firmware state of the MMIO copy [M, readings by testers and on the SB3]
| Device | MMIO PL1 / PL2 | enabled | MSR copy PL1 / PL2 (firmware) |
|---|---|---|---|
| Book 3 15" | 25 / 61 W | **no** (both enable bits 0; PL1 clamp 1, PL2 clamp 0; both lock bits 0; register `0x000001e800dd00c8`, [M] 23.11.2026, also seen through the module's zone) | 30 / 61 W (read during a control run, not the firmware value) |
| Pro 8 | 30 / 60 W | yes | 35 / 60 W |
| Pro 9 Intel | 30 / 60 W | yes | 35 / 60 W |
| Pro 10 | 28 / 35 W | yes | 35 / 64 W |
Further registers read on the SB3 [M, 24.11.2026, read-only through `/dev/mem`]: info `0x5930` = `0x78` (thermal spec 15.000 W, min 0, max 0, window 0, so no upper bound can be derived from the device), units `0x5938` = `0x000a0e03` (1/8 W, 1/16384 J, 1/1024 s), MMIO PL4 `0x59b0` field 87.625 W (effect unknown, the module does not touch it).
The lower enabled copy limits (`specs/deviations.md` DV-8): measured on the Pro 10 with four MSR values below the MMIO value, and on the SB3 with the copy enabled through the test module (PL1 8 W in the MMIO copy, 30 W in the MSR copy: package 8.0 W) [M, 24.11.2026]; on the Pro 8 and Pro 9 static only.
On the SB3 [M, 24.11.2026, `specs/kernel-mmio-rapl.md` 5.4]: a limit in the copy takes effect when its **enable** bit is set, with or without clamp, for PL1 and PL2 independently; the clamp alone does nothing; the firmware's state (PL1 enable 0, clamp 1) leaves a written limit without effect. The register survives s2idle; a cold boot restores the firmware value.

## 5. Open
- Access width of the Windows write (dword at `0x59a4` in most tables) versus the module's 64-bit read-modify-write (works on the SB3 [M]).
- The clamp on CPUs other than Ice Lake (on the SB3 not needed [M]); the clamp bits of the enabled firmware copies of the Pro 8, 9, 10 (not reported).
- Whether the firmware's lock bit (31/63) is set on any device (raw registers show lock 0 on the SB3 [M 23.11.2026], the Pro 9 and the Pro 10; not reported for the Pro 8).
- MCHBAR of types 4–8 on devices other than the Pro 9 and Pro 10; Lunar Lake in particular.
- Whether writing the MMIO copy has side effects beyond the limit: none seen in the SB3 tests of 23.–24.11.2026 (energy counter equal to the MSR zone's, MSR copy and the untouched register bits unchanged [M]); not measured elsewhere.
- The window rounding table of the SB3 client `8030e5ac` (read in the Pro 10 client, `research/12` 2.1).
