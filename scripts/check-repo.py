#!/usr/bin/env python3
"""Repository check for AC-X1 / NFR-8: no Microsoft binaries, decompiled code, blob content or the Windows product key.

Run from the repository root: python3 -I scripts/check-repo.py
It checks the files git tracks (or would track). Exit code 1 on any finding.
"""
import re
import subprocess
import sys

FORBIDDEN_SUFFIXES = (".sys", ".exe", ".dll", ".cat", ".inf", ".msi", ".cab", ".efivar", ".dsl", ".aml", ".dat", ".bin")
PATTERNS = [
    (re.compile(rb"FUN_14[0-9a-f]{7}"), "Ghidra function name (decompiled code)"),
    (re.compile(rb"DAT_14[0-9a-f]{7}"), "Ghidra data label (decompiled code)"),
    (re.compile(rb"\b[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}\b"), "product-key pattern"),
    (re.compile(rb"\x04\x01\x00\x27\x27\x00\x00\x00\x01\x00\x00\x00\x05\x00\x00\x00"), "SMF blob header"),
    (re.compile(rb"^(?:[0-9A-Fa-f-]{36},\d+,[-0-9.]+(?:,[-0-9.]+){2,}\r?\n){5,}", re.M), "block of blob channel lines"),
]


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True,
                         check=True).stdout
    return [f for f in out.splitlines() if f]


def main() -> int:
    findings = []
    for path in tracked_files():
        if path == "scripts/check-repo.py":
            continue
        if path.lower().endswith(FORBIDDEN_SUFFIXES):
            findings.append(f"{path}: forbidden file type")
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        for rx, what in PATTERNS:
            if rx.search(data):
                findings.append(f"{path}: {what}")
    for f in findings:
        print(f)
    print(f"{len(findings)} finding(s)" if findings else "repository check passed")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
