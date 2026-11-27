# Module signing, machine owner keys and the TPM

Evidence markers as in `docs/README.md`: [C] source code, [M] measured, [H] hypothesis. Background and the kit's rules: `docs/specs/tester-kit.md` 8.

## What the kernel does with a module it does not accept
- With `sig_enforce`, an **unsigned** module is refused with "Key was rejected by service" [M, Surface Book 3, 7.3.6-1-surface, 27.11.2026].
  A signature of a key the kernel does not know returns the same error, `EKEYREJECTED` [C, `kernel/module/signing.c`, `module_sig_check`,
  mainline `master` read 27.11.2026; not compared with the linux-surface tree], so the message alone does not tell the two apart. The kernel log
  line does: "Loading of unsigned module is rejected" [M] against "Loading of module with unavailable key is rejected" [C].
  The tester kit puts that line into its result.
- `sig_enforce` and lockdown decide, not Secure Boot [C, `module/signing.c`, `ima_efi.c`].

## Which keys are safe to enrol
- shim does not use a certificate with the extended key usage `1.3.6.1.4.1.2312.16.1.2` to check boot programs [C, shim 15, 15.4, 15.8, 16.1].
  A certificate without it, enrolled as a machine owner key, can sign boot programs.
- DKMS 3.4.5 makes its default key with `codeSigning` only, the fallback of DKMS 3.0.11 with no usage at all [C].
- A key limited that way is accepted for kernel modules [M, Surface Book 3, 7.3.6-1-surface, `CONFIG_INTEGRITY_CA_MACHINE_KEYRING` not set,
  27.11.2026: a module signed with it loaded, the same module unsigned was refused].
- Not checked: whether a running kernel accepts such a key for `kexec` [H].

## Reading the pending requests
- `MokNew` and `MokDel` are readable by root only. `mokutil --list-new` and `--list-delete` without root print nothing and exit 0, so
  "nothing pending" read that way proves nothing [M, Surface Book 3, mokutil 0.6.0, 27.11.2026]. The enrolled list (`--list-enrolled`) is
  readable without root [M].
- `mokutil --test-key` exits 1 also for an enrolled key: read the text [M].

## TPM: what enrolling and removing a key changes
One complete cycle on one machine [M, Surface Book 3 15", shim 15.8 (`shim-signed 1.58+15.8-0ubuntu1`), 27.11.2026; PCR bank sha256,
only the registers 4, 7, 8, 9 and 14 were compared]:

| State | Keys enrolled | Of PCR 4, 7, 8, 9, 14 | Disk unlock by the TPM (bound to these five) |
|---|---|---|---|
| before | 3 | reference | works (checked with the unlock script) |
| after enrolling a fourth key and rebooting | 4 | only PCR 14 differs | did not unlock; the passphrase was needed |
| after removing it and rebooting, nothing bound anew | 3 | all five as before | unlocked by itself |

- shim measures `MokList` and `MokListX` into PCR 14 [C, shim 15.8 `mok.c`].
- Only one boot was observed with the extra key. That later boots behave the same follows from the unchanged key list [H].
- Other machines, other bindings, the other registers and BitLocker: not checked [H].
