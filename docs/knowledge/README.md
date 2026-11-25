# Knowledge base

Established facts and known traps that are easy to get wrong, each with its evidence marker and source. Working rules (how to work, what needs
the maintainer's yes) are in `CLAUDE.md`; facts are here, so that they are versioned with the documentation and reach every reader, not only
an assistant. Started 24.11.2026.

| File | What is in it |
|---|---|
| [`rapl-power-limits.md`](rapl-power-limits.md) | the two copies of PL1/PL2 (MSR and MMIO), which one is in force, enable and clamp, what the kernel's RAPL core does with a write, reading the MMIO copy, thermald |
| [`sb3-test-practice.md`](sb3-test-practice.md) | how to run valid hardware tests on the Surface Book 3 15" (reference device): signing, suspend, services, measuring |
| [`sb3-detach-and-base.md`](sb3-detach-and-base.md) | detaching the Surface Book 3 base: what the base sensors reach in the blob, what Windows and Linux do on detach and re-attach, hwmon index changes, the dGPU Xid after a detach, DKMS and apt traps |

Rules for this folder:
- Every statement carries [C] (driver code, kernel or other source), [M] (measured, with device and date) or [H]; a source is named.
- A fact found on one device is stated for that device only. Nothing here is transferred to another device without its own evidence.
- When a fact changes (new measurement, correction), it is changed here in the same commit as the spec or plan it touches.
