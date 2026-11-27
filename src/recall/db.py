"""SQLite store: source of truth for command runs, plus the FTS5 (BM25) keyword index.

A *text* is a unique (redacted command, git repo) pair. Search ranks texts; each text
has one or more *runs* in `commands` carrying the per-execution metadata.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS cmd_texts (
    text_id    INTEGER PRIMARY KEY,
    text_hash  TEXT NOT NULL UNIQUE,
    cmd        TEXT NOT NULL,
    git_repo   TEXT,
    description TEXT,                        -- plain-English summary from the LLM (improves recall a lot)
    described  INTEGER NOT NULL DEFAULT 0,  -- 0 pending, 1 done, -1 gave up
    embed_text TEXT NOT NULL,
    embedded   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY,
    text_id     INTEGER NOT NULL REFERENCES cmd_texts(text_id),
    cmd         TEXT NOT NULL,
    ts          REAL,             -- unix seconds; NULL for imported history without timestamps
    duration_ms INTEGER,
    exit_code   INTEGER,
    cwd         TEXT,
    hostname    TEXT,
    session_id  TEXT,
    git_repo    TEXT,
    git_branch  TEXT,
    shell       TEXT,
    source      TEXT NOT NULL DEFAULT 'hook'
);
CREATE INDEX IF NOT EXISTS commands_text ON commands(text_id);
CREATE INDEX IF NOT EXISTS commands_ts ON commands(ts);

-- BM25 index over unique texts. porter stemming helps natural-language queries.
CREATE VIRTUAL TABLE IF NOT EXISTS cmd_fts USING fts5(
    cmd, description, git_repo, content='cmd_texts', content_rowid='text_id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS cmd_texts_ai AFTER INSERT ON cmd_texts BEGIN
    INSERT INTO cmd_fts(rowid, cmd, description, git_repo)
    VALUES (new.text_id, new.cmd, coalesce(new.description, ''), coalesce(new.git_repo, ''));
END;
CREATE TRIGGER IF NOT EXISTS cmd_texts_ad AFTER DELETE ON cmd_texts BEGIN
    INSERT INTO cmd_fts(cmd_fts, rowid, cmd, description, git_repo)
    VALUES ('delete', old.text_id, old.cmd, coalesce(old.description, ''), coalesce(old.git_repo, ''));
END;
CREATE TRIGGER IF NOT EXISTS cmd_texts_au AFTER UPDATE OF description ON cmd_texts BEGIN
    INSERT INTO cmd_fts(cmd_fts, rowid, cmd, description, git_repo)
    VALUES ('delete', old.text_id, old.cmd, coalesce(old.description, ''), coalesce(old.git_repo, ''));
    INSERT INTO cmd_fts(rowid, cmd, description, git_repo)
    VALUES (new.text_id, new.cmd, coalesce(new.description, ''), coalesce(new.git_repo, ''));
END;

-- Per-text aggregate used by search results.
CREATE VIEW IF NOT EXISTS text_stats AS
SELECT t.text_id, t.cmd, t.git_repo,
       count(c.id)                                   AS runs,
       sum(CASE WHEN c.exit_code != 0 THEN 1 ELSE 0 END) AS failures,
       max(c.ts)                                     AS last_ts
FROM cmd_texts t JOIN commands c ON c.text_id = t.text_id
GROUP BY t.text_id;
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    if get_meta(conn, "schema_version") is None:
        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def embed_text_for(cmd: str, git_repo: str | None, description: str | None = None) -> str:
    """Text sent to the embedding model: the command plus its description and light context.

    Embedding models understand terse shell syntax poorly ("df -h" vs "free disk space"),
    so the plain-English description carries most of the semantic signal.
    """
    parts = [cmd]
    if description:
        parts.append(description)
    if git_repo:
        parts.append(f"(repo: {git_repo})")
    return "\n".join(parts)


def get_or_create_text(conn: sqlite3.Connection, cmd: str, git_repo: str | None) -> int:
    h = hashlib.sha1(f"{cmd}\0{git_repo or ''}".encode()).hexdigest()
    row = conn.execute("SELECT text_id FROM cmd_texts WHERE text_hash = ?", (h,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO cmd_texts(text_hash, cmd, git_repo, embed_text) VALUES (?, ?, ?, ?)",
        (h, cmd, git_repo, embed_text_for(cmd, git_repo)),
    )
    return cur.lastrowid


def insert_run(conn: sqlite3.Connection, run: dict) -> int:
    text_id = get_or_create_text(conn, run["cmd"], run.get("git_repo"))
    cols = ["text_id", "cmd", "ts", "duration_ms", "exit_code", "cwd", "hostname", "session_id", "git_repo", "git_branch", "shell", "source"]
    vals = [text_id] + [run.get(c) for c in cols[1:]]
    if vals[-1] is None:
        vals[-1] = "hook"
    conn.execute(f"INSERT INTO commands({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    return text_id
