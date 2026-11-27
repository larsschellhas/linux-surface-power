"""Can this kernel load a module we build, and how (spec tester-kit.md 4.1 and 8).

What decides is `sig_enforce` and lockdown, not Secure Boot [C: kernel/module/signing.c, security/integrity/ima/ima_efi.c].
`insmod` has the last word.
"""
import os
from dataclasses import dataclass

from . import read, sh

# (private key, certificate): where distributions keep a module signing key. Ubuntu [M, SB3]; DKMS and akmods [H, not seen].
KEYS = (("var/lib/shim-signed/mok/MOK.priv", "var/lib/shim-signed/mok/MOK.der"),
        ("var/lib/dkms/mok.key", "var/lib/dkms/mok.pub"),
        ("etc/pki/akmods/private/private_key.priv", "etc/pki/akmods/certs/public_key.der"))
TEXT = {"A": "signature enforcement off and no lockdown: the module loads unsigned",
        "B": "signature enforcement on, a signing key is there",
        "C": "signature enforcement on, a key exists but it is not enrolled",
        "D": "signature enforcement on, no signing key of the distribution was found",
        "E": "kernel lockdown: the registers cannot be compared, the module stages do not run"}


@dataclass
class Signing:
    case: str
    key: str | None = None
    cert: str | None = None
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.case in ("A", "B")

    def describe(self) -> str:
        return f"case {self.case}: {TEXT[self.case]}" + (f" ({self.detail})" if self.detail else "")


def enrolment(cert: str) -> str:
    """"yes", "no" or "unknown". mokutil's text is read: version 0.6.0 exits with 1 for an enrolled key [M, SB3]."""
    code, text = sh.run(["mokutil", "--test-key", cert])
    if "is not enrolled" in text:
        return "no"
    if "already" in text:
        return "yes"
    return "unknown"


def decide(root: str = "/") -> Signing:
    lockdown = read(root, "sys/kernel/security/lockdown")
    if lockdown is not None and "[none]" not in lockdown:
        return Signing("E", detail=lockdown)
    # unreadable counts as enforced: then we sign if we can, and insmod decides
    if read(root, "sys/module/module/parameters/sig_enforce") == "N":
        return Signing("A")
    pairs = [(os.path.join(root, k), os.path.join(root, c)) for k, c in KEYS
             if os.path.exists(os.path.join(root, k)) and os.path.exists(os.path.join(root, c))]
    states = [(enrolment(c), k, c) for k, c in pairs]
    for want, detail in (("yes", "enrolled"), ("unknown", "enrolment could not be checked: insmod decides")):
        for state, key, cert in states:
            if state == want:
                return Signing("B", key, cert, f"{os.path.dirname(cert)}, {detail}")
    if states:
        return Signing("C", states[0][1], states[0][2], os.path.dirname(states[0][2]))
    return Signing("D")


def sign(signing: Signing, build_dir: str, module: str) -> tuple[bool, str]:
    if signing.case == "A":
        return True, "not signed (not needed)"
    code, text = sh.run([os.path.join(build_dir, "scripts/sign-file"), "sha512", signing.key, signing.cert, module])
    return code == 0, f"signed with the key in {os.path.dirname(signing.cert)}" if code == 0 else f"signing failed: {text}"
