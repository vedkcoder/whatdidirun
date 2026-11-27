"""Small display helpers shared by CLI and TUI."""

from __future__ import annotations

import os
import time


def ago(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "imported"
    s = (now or time.time()) - ts
    for unit, size in (("y", 365 * 86400), ("mo", 30 * 86400), ("w", 7 * 86400), ("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit} ago"
    return "just now"


def short_path(p: str | None) -> str:
    if not p:
        return ""
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p
