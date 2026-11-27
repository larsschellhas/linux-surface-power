"""K7: help a tester set up a key that their system trusts for kernel modules, and take it away again (spec tester-kit.md 8).

Enrolling a key is a lasting change to what the machine trusts, so it is a command of its own, never part of a check, and the
tester does the deciding steps: they type the one-time password into `mokutil` (the kit never sees it) and confirm at the blue
screen of the next boot.

The one rule that is never bent: **only a certificate that is limited to kernel modules is offered for enrolment.** shim does
not use a certificate that carries the extended key usage 1.3.6.1.4.1.2312.16.1.2 to check boot programs [C: shim 15, 15.4, 15.8
shim.c and 16.1 verify.c, `verify_eku`, which every check against the machine-owner key list goes through]. A certificate without
it, enrolled as a machine owner key, can sign boot programs that the machine then starts. The default keys of current DKMS (3.4.5:
`extendedKeyUsage=codeSigning` only) do not carry it [C: dkms.in].
"""
import json
import os
import re
import shutil

from . import read, sh, signing

MODSIGN_OID = "1.3.6.1.4.1.2312.16.1.2"
OWN_DIR = "var/lib/surface-power-check-signing"          # not under the markers' directory: those may be deleted
OWN_KEY, OWN_CERT = f"{OWN_DIR}/mok.priv", f"{OWN_DIR}/mok.der"
STATE = f"{OWN_DIR}/state.json"                          # what this kit enrolled here
UBUNTU_TOOL = "usr/sbin/update-secureboot-policy"
UBUNTU_KEY, UBUNTU_CERT = "var/lib/shim-signed/mok/MOK.priv", "var/lib/shim-signed/mok/MOK.der"
MOK_LIST = "sys/firmware/efi/efivars/MokListRT-605dab50-e046-4300-abb6-3dd810dd8b23"
ENROL_WORD, REMOVE_WORD = "enrol", "remove"

# The kit's own key: the same shape as Ubuntu's template [C: /usr/lib/shim/mok/openssl.cnf]. Its own config file, so that
# nothing of the system's openssl configuration adds to the certificate. No host name in the subject.
OPENSSL_CNF = f"""[ req ]
distinguished_name = dn
x509_extensions = module_signing
prompt = no

[ dn ]
CN = surface-power-check module signing key

[ module_signing ]
subjectKeyIdentifier = hash
basicConstraints = critical,CA:FALSE
extendedKeyUsage = codeSigning,{MODSIGN_OID}
"""

WHAT_IT_MEANS = (
    "The kernel module tests need a module that your kernel accepts. With signature enforcement on, that means a signature of a "
    "key your system trusts. Enrolling a key (a \"machine owner key\") adds one to that list. This is a lasting change; it stays "
    "until you remove it.",
    "What it allows: whoever has the private key can sign kernel modules that your kernel will load. The private key stays on "
    "this machine, readable by root only, so anyone who has root here can use it. A kernel module can do anything on the machine.",
    "What it does not allow: the key is limited to kernel modules. shim, the first program of your boot chain, refuses to start "
    "boot programs signed with it (checked in shim's source, versions 15 to 16.1). Not checked: whether a running kernel would "
    "accept it for starting another kernel (kexec).",
    "To undo it later: sudo ./scripts/surface-power-check --remove-signing")

# shim measures the machine owner key lists into TPM PCR 14 [C: shim 15.8 mok.c, MokList and MokListX], so adding or removing a
# key changes that register. Measured once, on the Surface Book 3 on 27.11.2026 (shim 15.8, unlock bound to 4, 7, 8, 9 and 14;
# docs/knowledge/module-signing.md): after enrolling only PCR 14 of these had changed and the passphrase was needed; after the
# removal it had its old value and the disk unlocked by itself. Other registers, other bindings and Windows' BitLocker are not
# checked, so the text asks to be ready for both.
DISK_WARNING = (
    "IS YOUR DISK UNLOCKED AUTOMATICALLY BY THE TPM? Adding or removing a key changes what the boot chain measures into the TPM. "
    "At the next boot you may be asked for your disk passphrase or recovery key, and you may have to set up the automatic unlock "
    "again. Have the passphrase or recovery key at hand BEFORE you go on; without it you could lock yourself out. If Windows with "
    "BitLocker is on this machine too, have its recovery key at hand as well.")

DISK_SEEN = (
    "Tried so far on one machine (a Surface Book 3 whose unlock is bound to TPM registers 4, 7, 8, 9 and 14): after adding the "
    "key the passphrase was needed at boot; of these registers only 14 had changed. After removing the key it had its old value "
    "and the disk unlocked by itself again, with nothing set up anew. As long as the key stays enrolled, expect to type the "
    "passphrase at every boot, or set up the automatic unlock again (and then possibly once more after the removal). Your "
    "machine may behave differently.")

BLUE_SCREEN = (
    "Restart the computer. Before Linux starts, a blue screen appears (\"Perform MOK management\"). It is the same screen you "
    "see when setting up Secure Boot for linux-surface. It waits only a few seconds for a key press; if it passes, run this "
    "{command} again.")


def at_the_next_boot(name: str) -> tuple[str, ...]:
    return (
        BLUE_SCREEN.format(command="setup"),
        f"Choose Enroll MOK, then View key 0. It must show CN={name}. If it shows something else, or there is a further key "
        "whose origin you do not know, do not confirm: choose Continue boot.",
        "Go back, choose Continue, then Yes. Type the password you just set (the keyboard layout there may be the US one). Then "
        "choose Reboot.",
        "After the restart, run the check again: sudo ./scripts/surface-power-check")


def at_the_removal_boot(name: str) -> tuple[str, ...]:
    return (
        BLUE_SCREEN.format(command="removal"),
        f"Choose Delete MOK, then View key 0. It must show CN={name}, and there must be no further key. If you see any other "
        "key, do NOT confirm: removing the wrong key can stop your system from starting. Choose Continue boot.",
        "Go back, choose Continue, then Yes. Type the password you just set, then choose Reboot.",
        "After the restart, run --remove-signing once more: it checks that the key is gone and tidies up.")


class Stop(Exception):
    """The setup cannot go on; the text says why."""


def module_only(cert: str) -> bool | None:
    """True if the certificate carries the module-signing-only usage, False if not, None if it cannot be read."""
    for form in ("der", "pem"):
        code, text = sh.run(["openssl", "x509", "-inform", form, "-in", cert, "-noout", "-ext", "extendedKeyUsage"])
        if code == 0:
            return MODSIGN_OID in text
    return None


def fingerprint(cert: str) -> str | None:
    for form in ("der", "pem"):
        code, text = sh.run(["openssl", "x509", "-inform", form, "-in", cert, "-noout", "-fingerprint", "-sha1"])
        if code == 0 and "=" in text:
            return text.rsplit("=", 1)[1].replace(":", "").strip().lower()
    return None


def common_name(cert: str) -> str:
    """The name the blue screen shows for the certificate, to compare before confirming there."""
    for form in ("der", "pem"):
        code, text = sh.run(["openssl", "x509", "-inform", form, "-in", cert, "-noout", "-subject", "-nameopt", "multiline"])
        if code == 0:
            found = re.search(r"commonName\s*=\s*(.+)", text)
            if found:
                return found.group(1).strip()
    return "(the name could not be read: compare with `openssl x509 -inform der -in " + cert + " -noout -subject`)"


def _disk_warning(term) -> None:
    term.text(DISK_WARNING, 2, "bold", "yellow")
    term.line()
    term.text(DISK_SEEN, 2, "yellow")


def _listed(option: str) -> str:
    """What `mokutil <option>` lists, as lower-case hex without colons, to look fingerprints up in."""
    _, text = sh.run(["mokutil", option])
    return re.sub(r"[:\s]", "", text).lower()


def pending(cert: str, option: str = "--list-new") -> bool:
    mark = fingerprint(cert)
    return mark is not None and mark in _listed(option)


def kernel_config(root: str, release: str) -> str | None:
    return read(root, f"boot/config-{release}") or read(root, f"usr/lib/modules/{release}/config")


def prechecks(root: str, release: str, second_key: bool = False) -> list[str]:
    """Stops if enrolling cannot work here; returns remarks that do not stop it. `second_key`: the maintainer's test of this setup
    enrols a key although one works already; every other check stays."""
    now = signing.decide(root)
    if not second_key:
        if now.case == "A":
            raise Stop("Nothing to set up: signature enforcement is off and the kernel is not locked down, so the module loads "
                       "unsigned.")
        if now.case == "B":
            raise Stop(f"Nothing to set up: there is a key your system trusts already ({os.path.dirname(now.cert)}).")
    if now.case == "E":
        raise Stop("A key would not help: the kernel is locked down, and then the module tests cannot compare registers.")
    if not shutil.which("mokutil") or not shutil.which("openssl"):
        raise Stop("This needs the programs mokutil and openssl. Please install them with your distribution's package manager.")
    if not os.path.exists(os.path.join(root, MOK_LIST)):
        raise Stop("Your system does not seem to start through shim (there is no machine owner key list). Enrolling a key with "
                   "mokutil would have no effect here.")
    config = kernel_config(root, release)
    if config is None:
        return ["The kernel's build configuration could not be read: whether it trusts such a key for modules shows when the "
                "module is loaded."]
    if "CONFIG_INTEGRITY_CA_MACHINE_KEYRING=y" in config:
        raise Stop("Your kernel only trusts certificate-authority keys from the machine owner list "
                   "(CONFIG_INTEGRITY_CA_MACHINE_KEYRING). A key limited to modules would not be accepted, and this kit enrols "
                   "no other kind.")
    if "CONFIG_INTEGRITY_MACHINE_KEYRING=y" not in config:
        return ["Your kernel's configuration does not show the machine owner keyring: it may not accept an enrolled key for "
                "modules. The module test will tell."]
    return []


def _pairs(root: str):
    for key, cert in signing.KEYS:
        k, c = os.path.join(root, key), os.path.join(root, cert)
        if os.path.exists(k) and os.path.exists(c):
            yield k, c


def create_own(root: str) -> tuple[str, str]:
    directory = os.path.join(root, OWN_DIR)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    config = os.path.join(directory, "openssl.cnf")
    with open(config, "w") as f:
        f.write(OPENSSL_CNF)
    key, cert = os.path.join(root, OWN_KEY), os.path.join(root, OWN_CERT)
    before = os.umask(0o077)                        # the private key is never readable by others, not even for a moment
    try:
        code, text = sh.run(["openssl", "req", "-config", config, "-new", "-x509", "-newkey", "rsa:2048", "-nodes", "-days",
                             "3650", "-outform", "DER", "-keyout", key, "-out", cert])
    finally:
        os.umask(before)
    if code != 0 or not os.path.exists(cert):
        raise Stop(f"The key could not be created: {text}")
    os.chmod(cert, 0o644)
    return key, cert


def choose_key(root: str, say, own: bool = False) -> tuple[str, str, str]:
    """(private key, certificate, who made it: "existing" | "ubuntu" | "kit"). Only a module-only certificate comes back."""
    if not own:
        for key, cert in _pairs(root):
            limited = module_only(cert)
            if limited:
                made = "kit" if cert == os.path.join(root, OWN_CERT) else "existing"
                say(f"There is a key already, limited to kernel modules: {os.path.dirname(cert)}.")
                return key, cert, made
            why = "it is not limited to kernel modules, so it could also sign boot programs" if limited is False else "it cannot be read"
            say(f"The key in {os.path.dirname(cert)} is left alone: {why}. This kit does not enrol it.")
    if own and os.path.exists(os.path.join(root, OWN_CERT)) and os.path.exists(os.path.join(root, OWN_KEY)):
        return os.path.join(root, OWN_KEY), os.path.join(root, OWN_CERT), "kit"
    ubuntu = os.path.join(root, UBUNTU_CERT)
    if not own and os.path.exists(os.path.join(root, UBUNTU_TOOL)) and not os.path.exists(ubuntu):
        code, _ = sh.run([os.path.join(root, UBUNTU_TOOL), "--new-key"], env={**os.environ, "SHIM_NOTRIGGER": "y"})
        if code == 0 and os.path.exists(ubuntu) and os.path.exists(os.path.join(root, UBUNTU_KEY)):
            say("Created your distribution's signing key with update-secureboot-policy (DKMS uses the same one).")
            return os.path.join(root, UBUNTU_KEY), ubuntu, "ubuntu"
    say(f"Creating a key of this kit, limited to kernel modules, in /{OWN_DIR} (readable by root only).")
    key, cert = create_own(root)
    return key, cert, "kit"


def load_state(root: str) -> dict | None:
    try:
        with open(os.path.join(root, STATE)) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("cert") else None


def save_state(root: str, state: dict) -> None:
    os.makedirs(os.path.join(root, OWN_DIR), mode=0o700, exist_ok=True)
    with open(os.path.join(root, STATE), "w") as f:
        json.dump(state, f)


def setup(root: str, release: str, term, ask_line, interactive, own: bool = False) -> int:
    """`interactive(cmd) -> exit code` runs a command on the tester's own terminal (mokutil asks for the password there)."""
    term.heading("Setting up module signing")
    try:
        # a second key next to a working one is only for the maintainer's own test of this setup
        remarks = prechecks(root, release, second_key=own)
        for i, text in enumerate(WHAT_IT_MEANS):
            if i:
                term.line()
            term.text(text)
        for text in remarks:
            term.line()
            term.bullet(text)
        term.line()
        _disk_warning(term)
        term.line()
        term.text("Nothing has been changed yet.", 2, "bold")
        if ask_line(f"  > Type \"{ENROL_WORD}\" to go on, or press Enter to stop: ").strip().strip('"').lower() != ENROL_WORD:
            term.text("Stopped. Nothing was changed.")
            return 0
        key, cert, made_by = choose_key(root, term.text, own)
        if module_only(cert) is not True:                       # the rule, checked again on the file that goes to mokutil
            raise Stop(f"The certificate {cert} is not limited to kernel modules: it is not enrolled.")
        if pending(cert):
            term.text("This key is already waiting to be enrolled at the next boot.")
        else:
            term.line()
            term.text("mokutil now asks for a one-time password, twice. Choose a simple one that you can type at the next boot; "
                      "this kit does not see or keep it.")
            code = interactive(["mokutil", "--import", cert])
            if code != 0 or not pending(cert):
                raise Stop("mokutil did not take the key over. Nothing waits for the next boot.")
        save_state(root, {"cert": cert, "key": key, "made_by": made_by, "fingerprint": fingerprint(cert)})
    except Stop as e:
        term.line()
        term.text(str(e))
        return 1 if not str(e).startswith("Nothing to set up") else 0
    term.heading("What to do now")
    for text in at_the_next_boot(common_name(cert)):
        term.bullet(text)
    return 0


def remove(root: str, term, ask_line, interactive) -> int:
    term.heading("Removing module signing")
    state = load_state(root)
    if state is None:
        term.text("This kit has enrolled no key on this machine, so there is nothing of its own to remove. Keys that were there "
                  "before stay.")
        return 0
    cert, made_by = state["cert"], state.get("made_by")
    if not os.path.exists(cert):
        term.text(f"The certificate {cert} is gone. `mokutil --list-enrolled` shows what your system trusts.")
        return 1
    waiting, known = pending(cert), signing.enrolment(cert)
    if not waiting and known == "no":                     # only a clear "not enrolled" lets the record and our files go
        _forget(root, made_by, term)
        term.text("The key is not enrolled (any more).")
        return 0
    if not waiting and known != "yes":
        term.text(f"mokutil does not say clearly whether the key in {os.path.dirname(cert)} is enrolled, so nothing is removed "
                  f"and nothing is deleted. `mokutil --test-key {cert}` shows what it answers.")
        return 1
    term.text(f"This takes the key in {os.path.dirname(cert)} out of the list your system trusts, at the next boot.")
    if not waiting:                                        # a request that only waits has changed nothing yet
        term.line()
        _disk_warning(term)
        term.line()
    if ask_line(f"  > Type \"{REMOVE_WORD}\" to go on, or press Enter to stop: ").strip().strip('"').lower() != REMOVE_WORD:
        term.text("Stopped. Nothing was changed.")
        return 0
    if waiting:
        _, listing = sh.run(["mokutil", "--list-new"])
        if listing.count("SHA1 Fingerprint") > 1:
            term.text("Other keys are waiting to be enrolled as well. Withdrawing would drop them too, so this kit does not do it. "
                      "Skip the blue screen at the next boot (do not choose Enroll MOK), then run this command again.")
            return 1
        if interactive(["mokutil", "--revoke-import"]) != 0:
            term.text("mokutil could not withdraw the request.")
            return 1
        _forget(root, made_by, term)
        term.text("The waiting request is withdrawn: nothing will be enrolled.")
        return 0
    term.text("mokutil now asks for a one-time password, twice; you type it again at the blue screen.")
    if interactive(["mokutil", "--delete", cert]) != 0 or not pending(cert, "--list-delete"):
        term.text("mokutil did not take the removal over.")
        return 1
    if made_by == "kit" and os.path.exists(os.path.join(root, OWN_KEY)):
        os.remove(os.path.join(root, OWN_KEY))               # without it nothing new can be signed, whatever happens at the boot
        term.text("The private key is deleted: nothing new can be signed with it.")
    term.heading("What to do now")
    for text in at_the_removal_boot(common_name(cert)):
        term.bullet(text)
    return 0


def _forget(root: str, made_by: str | None, term) -> None:
    """Drop the record, and the files this kit made itself. A distribution's key stays: other modules may use it."""
    if made_by == "kit":
        shutil.rmtree(os.path.join(root, OWN_DIR), ignore_errors=True)
        return
    if os.path.exists(os.path.join(root, STATE)):
        os.remove(os.path.join(root, STATE))
    term.text("The key files stay: they are your distribution's, and other modules may be signed with them.")
