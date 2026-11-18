NOTE: the complete, current instructions are in INSTRUCTIONS.txt. This file is background only.

# Raw register capture on Windows (Surface Pro 10 for Business)

Goal: find out whether Windows rewrites the memory-mapped power limit (MCHBAR+0x59A0), what Windows leaves in the MSR copies, and what the
real PL4 is. Windows is used only for these runs, so the setup is Windows-only; do not touch Secure Boot or other firmware settings.
Reads only: do not write any register, do not press "save", "apply" or "set" in any tool.

## Registers to read (all 64-bit, give the full hex value)

| Id | Register | Linux boot value (for comparison) | Decodes as |
|---|---|---|---|
| m610 | MSR `0x610` package power limit | `42820000dd8118` | PL1 35 W, PL2 64 W, window 28 s |
| mmio59a0 | physical memory `0xFEDC59A0` (MCHBAR+0x59A0) | `0000811800DD80E0` | PL1 28 W, PL2 35 W, window 28 s |
| m65c | MSR `0x65C` platform (psys) limit | `824000dd8180` | PL1 48 W, PL2 72 W |
| m601 | MSR `0x601` PL4 | `2b0` | 86 W (set by an interim Linux driver, not the firmware value) |
| m615 | MSR `0x615` PL3 | `a4882f8` | 95 W |
| m1a2 | MSR `0x1A2` temperature target | `6e00b1` | TjMax 110 C, offset 0 |
| mmio5b10 | physical memory `0xFEDC5B10` (MCHBAR+0x5B10) | `0` | unidentified |
| u606 | MSR `0x606` units | `a0e03` | 1/8 W, 2^-14 J, 1/1024 s |

If Windows shows a register as 32-bit halves, read both halves (`0x59A0` and `0x59A4`) and join them as `high` then `low`.

## Tools (not verified on this machine; try one, stop if it asks for anything beyond reading)

- **MSRs (`0x610`, `0x65C`, `0x601`, `0x615`, `0x1A2`, `0x606`):** a hardware monitor or a register-reading utility that lists model-specific
  registers. Some show only decoded power limits; that is still useful for `0x610`, but the raw hex is better.
- **Physical memory `0xFEDC59A0` and `0xFEDC5B10`:** a utility with a "memory" or "physical address" viewer. Windows may refuse to load it. If so,
  turn off *Windows Security, Device security, Core isolation, Memory integrity* (Windows-only setting; reboot), and if it is still blocked
  also check the "Microsoft vulnerable driver blocklist" switch on the same page. Turn them back on afterwards if you want.
- Not needed: a kernel debugger. It requires debug mode, which requires Secure Boot to be off, which affects Linux too.

## Procedure (about 45 minutes, one boot)

1. Boot Windows on AC, power mode "best performance", wait 10 minutes idle. Close everything else.
2. **Idle read (t = idle).** Read all registers in the table. Write the values in the form at the bottom.
3. Start the load: `powershell -ExecutionPolicy Bypass -File .\run-continuous.ps1 -Mode best -Power ac`.
4. **Loaded reads** at about 1 min, 8 min, 14 min and 19 min into the run. The Linux dry-run steps PL1 down at about 12 minutes, so the
   14 and 19 minute reads show whether the limit registers change with it. Reading while the run records is fine; do it quickly.
5. After the run stops, wait 5 minutes and read once more (**after load**).
6. Decode on Linux (or anywhere with Python):
   `python3 decode-registers.py --u606 <v> --m610 <v> --mmio59a0 <v> --m65c <v> --m601 <v> --m615 <v> --m1a2 <v> --mmio5b10 <v>`
   Paste the decoded lines next to the raw values.

## What the answers mean

- `mmio59a0` differs from `0000811800DD80E0` at idle: Windows (or its firmware service) rewrites the MMIO copy. The Linux daemon needs an
  MMIO output (and our kernel patch to expose it).
- `mmio59a0` still reads 28 W / 35 W and `m610` changes: Windows only writes the MSR copy. The lower copy then binds as on Linux, and an
  MSR-only Linux daemon matches Windows for reductions.
- `m601` is the real PL4 Windows sets; compare with Linux 86 W (interim driver) and the daemon's default (120 W in the blob table).
- Any register that changes between idle and the loaded reads is a register the controller drives; note when it changes.

## Form (copy for each read)

```
time label: idle | 1 min | 8 min | 14 min | 19 min | after load
u606:
m610:
mmio59a0:
m65c:
m601:
m615:
m1a2:
mmio5b10:
```
