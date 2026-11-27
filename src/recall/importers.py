"""Import existing shell history files (bash, PowerShell/PSReadLine)."""

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


def parse_psreadline_history(text: str) -> list[tuple[float | None, str]]:
    """Parse PSReadLine's ConsoleHost_history.txt (no timestamps).

    PSReadLine saves a multi-line command as several lines, each but the last ending in a backtick.
    """
    entries: list[tuple[float | None, str]] = []
    buf: list[str] = []
    for line in text.splitlines():
        if line.endswith("`"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        cmd = "\n".join(buf)
        buf = []
        if cmd.strip():
            entries.append((None, cmd))
    if buf and "\n".join(buf).strip():
        entries.append((None, "\n".join(buf)))
    return entries


def psreadline_history_path() -> Path:
    """Default PSReadLine history file for the console host (Windows; also PowerShell 7 on Linux/macOS)."""
    import os

    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        return base / "powershell/PSReadLine/ConsoleHost_history.txt"
    return base / "Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt"


def detect_format(path: Path) -> str:
    return "psreadline" if path.name.lower().endswith("_history.txt") else "bash"


def import_history(conn: sqlite3.Connection, path: Path, ignore: list[re.Pattern[str]], fmt: str = "auto") -> tuple[int, int]:
    """Import a bash or PSReadLine history file. Returns (imported, skipped). Re-importing is idempotent."""
    fmt = detect_format(path) if fmt == "auto" else fmt
    if fmt not in ("bash", "psreadline"):
        raise ValueError(f"unknown history format {fmt!r}")
    parse, shell = (parse_psreadline_history, "pwsh") if fmt == "psreadline" else (parse_bash_history, "bash")
    imported = skipped = 0
    for ts, cmd in parse(path.read_text(encoding="utf-8", errors="replace")):
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
        insert_run(conn, {"cmd": cmd, "ts": ts, "shell": shell, "source": "import"})
        imported += 1
    conn.commit()
    return imported, skipped


def import_bash_history(conn: sqlite3.Connection, path: Path, ignore: list[re.Pattern[str]]) -> tuple[int, int]:
    return import_history(conn, path, ignore, "bash")
