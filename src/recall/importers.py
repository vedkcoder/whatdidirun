"""Import existing shell history files."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .db import get_or_create_text, insert_run
from .redact import redact

_TS_LINE = re.compile(r"^#(\d{9,11})$")


def parse_bash_history(text: str) -> list[tuple[float | None, str]]:
    """Parse ~/.bash_history. Supports HISTTIMEFORMAT-style '#<epoch>' lines before each entry."""
    entries: list[tuple[float | None, str]] = []
    ts: float | None = None
    for line in text.splitlines():
        if m := _TS_LINE.match(line):
            ts = float(m.group(1))
            continue
        if line.strip():
            entries.append((ts, line))
        ts = None
    return entries


def import_bash_history(conn: sqlite3.Connection, path: Path, ignore: list[re.Pattern[str]]) -> tuple[int, int]:
    """Returns (imported, skipped). Re-importing the same file is idempotent."""
    imported = skipped = 0
    for ts, cmd in parse_bash_history(path.read_text(errors="replace")):
        if cmd.startswith(" ") or any(p.search(cmd.strip()) for p in ignore):
            skipped += 1
            continue
        cmd = redact(cmd.strip())
        text_id = get_or_create_text(conn, cmd, None)
        # Idempotency: one imported run per (text, timestamp). Untimestamped repeats collapse to one.
        exists = conn.execute(
            "SELECT 1 FROM commands WHERE text_id = ? AND source = 'import' AND ts IS ?", (text_id, ts)
        ).fetchone()
        if exists:
            skipped += 1
            continue
        insert_run(conn, {"cmd": cmd, "ts": ts, "shell": "bash", "source": "import"})
        imported += 1
    conn.commit()
    return imported, skipped
