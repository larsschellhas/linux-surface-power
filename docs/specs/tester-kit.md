# Spec: tester kit (one command for a device check)

Version 0.2, 25.11.2026. **Implemented 25.11.2026** (`scripts/surface-power-check`, `src/surface_power/tools/check.py`, `.github/ISSUE_TEMPLATE/device-report.yml`, `tests/test_check.py`); not yet run by a tester. Wish of the maintainer (25.11.2026): a tester runs one script with `sudo`; it builds what is needed,
runs every check that is needed on the device, and the result goes into a device-specific issue of this repository, opened by the tester. Markers as in
`blob-format.md`. Decisions open for the maintainer are in section 6.

## 1. Goal
- One command, no reading of documents before: `sudo ./scripts/surface-power-check` from a checkout (or a release archive).
- Nothing is changed on the device for good: no installation, no unit enabled, no limit written, no module left loaded; every step that could matter asks first.
- The result is one text file without serial numbers or blob content, and one prepared issue in `larsschellhas/linux-surface-power` per device model.

## 2. Steps
1. **Preflight** (read only): Surface vendor, SMF present, kernel and linux-surface modules, Secure Boot and lockdown state, CPU vendor, running writers
   (thermald, tuned, TLP, power-profiles-daemon), whether `surface_aggregator_cdev` is loaded. The script says up front what it will do and asks once.
2. **Report:** `surface-power-report --with-ec` as root, from the checkout (no installation). `surface_aggregator_cdev` is loaded for the run if it is not
   loaded and unloaded again afterwards [D, open: 6.3].
3. **Short dry-run** (2 min) of the daemon in a transient unit with the sandbox of `surface-powerd-dryrun.service` (`systemd-run`, the script outside `/tmp`):
   status and journal of the run go into the result. Nothing is written (dry-run).
4. **Module, read only** (only on Intel devices with a profile and a platform type, only if the tester agrees, `kernel-mmio-rapl.md` 6.1 item 3):
   build `surface_rapl_mmio` against the running kernel's headers, sign it where Secure Boot requires it and a usable key exists (6.2), load it with
   `allow_write=0` and `unsafe_platform=<type of the profile>` (taints the kernel, `kernel-mmio-rapl.md` 6.3), compare the zone with the register
   (`scripts/mmio-pl-read.py`), unload, check that the register is bit-identical. Skipped with a clear line when headers, build tools or a key are missing.
5. **Result:** one file `surface-power-check-<model>-<date>.txt`; the script prints its path and the next step (7).

## 3. What the script never does
Write a limit, write `enabled`, load the module with `allow_write=1`, install or enable a unit, change the MOK list or Secure Boot, send anything to the internet.

## 4. Privacy
The report already contains no serial numbers and no blob content (spec tools 3). The kit adds no MAC addresses, user names or host names; journal lines are
the daemon's own. The tester sees the whole file before anything is shared.

## 5. Issue
- One issue per device model ("Device report: Surface Pro 9"), with an issue template `device-report.yml` (model, SKU, kernel, what was run, the file).
- If `gh` is installed and logged in: the script asks, then searches for the model's issue and adds a comment with the file, or opens the issue. Else it
  prints a link to the issue search and to "new issue" with the template and the title filled in; the tester pastes the file (a URL cannot carry the whole
  report, GitHub limits its length) [H for the exact limit].

## 6. Decisions (maintainer, 25.11.2026)
1. **Module test:** on every Intel device with a profile (the platform type per profile is in `check.PLATFORM`, from `research/15` 2; a test keeps the
   table complete); not on AMD, not with 00:04.0 present. The tester is asked once more before the module is loaded.
2. **Signing:** an existing DKMS key of the distribution (Ubuntu `/var/lib/shim-signed/mok/`, Fedora/Arch `/var/lib/dkms/mok.key`); without one under Secure
   Boot the module test is skipped with a clear line, no new MOK is enrolled.
3. **cdev:** loaded for the run when missing and unloaded at the end.
4. **Dry-run:** 120 s; running writers are reported, not stopped [D, proposal taken].
5. **Issues:** one issue per model in this repository ("Device report: <product>"); with `gh` logged in the script asks, then comments on the model's issue or
   opens it as the tester; else it prints the search link and the new-issue link with the template. `--no-issue` skips the step.
