"""PyInstaller entry point for WhatDidIRun.exe (windowed tray app, no console)."""

import os
import sys

# A windowed exe has no console: give libraries that print somewhere harmless to write.
for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w"))

from recall.config import load_config  # noqa: E402
from recall.tray import run  # noqa: E402

if __name__ == "__main__":
    run(load_config())
