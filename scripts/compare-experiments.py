#!/usr/bin/env python3
"""Prepare, run and export matched Factory / native Codex-goal driver experiments."""

import sys
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parents[1] / "src"),
    str(Path(__file__).resolve().parent / "native_experiment"),
]
from comparison import main

if __name__ == "__main__":
    main()
