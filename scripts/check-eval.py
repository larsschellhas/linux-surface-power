#!/usr/bin/env python3
"""Skeleton of an evaluation from a tester kit result: `python3 scripts/check-eval.py FILE` or `--issue N` (spec tester-kit.md 9)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "src"))
from surface_power.tools.check_eval import main  # noqa: E402

sys.exit(main())
