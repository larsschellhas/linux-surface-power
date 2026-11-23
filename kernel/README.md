# Kernel modules (prototypes)

Out-of-tree prototypes for the interfaces the SMF daemon needs from the kernel. They are independent of the daemon and meant to become patches for the
linux-surface kernel and later for mainline. Concept and requirements: [`docs/requirements/kernel-modules.md`](../docs/requirements/kernel-modules.md).

**License:** GPL-2.0-only (SPDX line in every file; text in [`LICENSES/preferred/GPL-2.0`](LICENSES/preferred/GPL-2.0), taken from the linux-surface kernel tree). They use GPL-only kernel interfaces, so the project's MIT/Apache licenses do not apply to this directory.
**AI assistance:** commits here carry `Assisted-by: LLM [tools]` as the kernel asks; `Signed-off-by` is the maintainer's alone (`CLAUDE.md` "Kernel modules").

| Directory | Module | Spec | State |
|---|---|---|---|
| `surface-rapl-mmio/` | `surface_rapl_mmio`: powercap zone for the MMIO copy of PL1/PL2 | [`docs/specs/kernel-mmio-rapl.md`](../docs/specs/kernel-mmio-rapl.md) | skeleton, compiles; not loaded on any device yet |
| (not started) | `surface_pl3`: PL3 from the kernel | [`docs/specs/kernel-pl3.md`](../docs/specs/kernel-pl3.md) | concept |

**Not for everyone:** the module acts only on devices in its DMI table, is read-only unless loaded with `allow_write=1`, and with that taints the kernel. It
needs a matching kernel header package and, with Secure Boot, your own module signing (the prototype does not meet the project's "no extra setup" goal; the
final form in the linux-surface kernel will). Build in a copy of the directory: `cp -r kernel/surface-rapl-mmio /tmp/srm && make -C /tmp/srm`.
Do not load it on a device before the evidence steps in the spec (section 6) and the test plan (section 8) are done.
