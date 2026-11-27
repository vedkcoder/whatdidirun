"""Move captured commands from the JSONL spool into SQLite, then embed new texts into Chroma."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .db import insert_run
from .redact import redact


@dataclass
class IngestResult:
    stored: int = 0
    skipped: int = 0
    described: int = 0
    embedded: int = 0
    warnings: list[str] = field(default_factory=list)


if os.name == "nt":
    import msvcrt

    def _try_lock(fh) -> bool:
        fh.seek(0)  # msvcrt locks from the current position; "a+" starts at EOF
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)


@contextmanager
def _lock(path: Path, blocking: bool = True, poll: float = 0.1):
    """Exclusive inter-process lock on `path`. Yields False instead of waiting when blocking=False and it's held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        while not _try_lock(fh):
            if not blocking:
                yield False
                return
            time.sleep(poll)
        try:
            yield True
        finally:
            _unlock(fh)


def git_info(cwd: str | None, _cache: dict[str, tuple[str | None, str | None]] = {}) -> tuple[str | None, str | None]:
    """(repo name, branch) for a directory, found by walking up to a .git entry. No subprocess."""
    if not cwd:
        return None, None
    if cwd in _cache:
        return _cache[cwd]
    result: tuple[str | None, str | None] = (None, None)
    p = Path(cwd)
    for d in [p, *p.parents]:
        git = d / ".git"
        try:
            if git.is_file():  # worktree / submodule: "gitdir: <path>"
                gitdir = Path(git.read_text().split(":", 1)[1].strip())
                git = gitdir if gitdir.is_absolute() else (d / gitdir)
            if git.is_dir():
                head = (git / "HEAD").read_text().strip()
                branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref:") else head[:12]
                result = (d.name, branch)
                break
        except OSError:
            continue
    _cache[cwd] = result
    return result


def _ms(start: str | None, end: str | None) -> int | None:
    try:
        return max(0, round((float(end) - float(start)) * 1000))
    except (TypeError, ValueError):
        return None


def prepare_run(rec: dict, ignore: list[re.Pattern[str]]) -> dict | None:
    """Turn a raw spool record into a row for `commands`, or None to drop it."""
    cmd = rec.get("cmd") or ""
    if cmd.startswith(" "):  # leading space = "don't record this" (HISTCONTROL=ignorespace convention)
        return None
    cmd = cmd.strip()
    if not cmd or any(p.search(cmd) for p in ignore):
        return None
    repo, branch = git_info(rec.get("cwd"))
    try:
        ts = float(rec["start"])
    except (KeyError, TypeError, ValueError):
        ts = time.time()
    try:
        exit_code = int(rec.get("exit"))
    except (TypeError, ValueError):
        exit_code = None
    return {
        "cmd": redact(cmd),
        "ts": ts,
        "duration_ms": _ms(rec.get("start"), rec.get("end")),
        "exit_code": exit_code,
        "cwd": rec.get("cwd"),
        "hostname": rec.get("host"),
        "session_id": rec.get("session"),
        "git_repo": repo,
        "git_branch": branch,
        "shell": rec.get("shell", "bash"),
        "source": "hook",
    }


def ingest_spool(conn: sqlite3.Connection, cfg: Config) -> tuple[int, int]:
    spool = cfg.spool_path
    # Atomically take ownership of the current spool; shells open it with >> per command,
    # so new commands land in a fresh file. Leftovers from a crashed run are picked up too.
    if spool.exists():
        target = spool.with_name(f"spool.{os.getpid()}.{time.time_ns()}.processing")
        for attempt in range(5):
            try:
                spool.rename(target)
                break
            except PermissionError:  # Windows: a shell is appending right now; retry, else leave it for next time
                time.sleep(0.05 * (attempt + 1))
    ignore = [re.compile(p) for p in cfg.ignore_patterns]
    stored = skipped = 0
    for chunk in sorted(spool.parent.glob("spool.*.processing")):
        for line in chunk.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line, strict=False)  # strict=False tolerates raw control chars
            except json.JSONDecodeError:
                skipped += 1
                continue
            run = prepare_run(rec, ignore)
            if run is None:
                skipped += 1
                continue
            insert_run(conn, run)
            stored += 1
        conn.commit()
        chunk.unlink()
    return stored, skipped


def ingest(
    cfg: Config, conn: sqlite3.Connection, embed: bool = True, describe: bool | None = None, wait: bool = True, lm=None, vi=None
) -> IngestResult:
    """Spool -> SQLite, then (optionally) describe new commands with the LLM and embed them into Chroma.

    Everything runs under one file lock so concurrent shells / the web server never double-process.
    """
    res = IngestResult()
    describe = cfg.describe and cfg.llm_enabled if describe is None else describe
    with _lock(cfg.data_dir / "ingest.lock", blocking=wait) as got:
        if not got:
            res.warnings.append("another ingest is running")
            return res
        res.stored, res.skipped = ingest_spool(conn, cfg)
        if embed or describe:
            res.described, res.embedded, warns = enrich_and_embed(cfg, conn, embed=embed, describe=describe, lm=lm, vi=vi)
            res.warnings += warns
    return res


def enrich_and_embed(cfg: Config, conn: sqlite3.Connection, embed: bool = True, describe: bool = True, lm=None, vi=None) -> tuple[int, int, list[str]]:
    from .enrich import describe_pending
    from .lmstudio import LMStudio, LMStudioError
    from .vectors import VectorIndex

    lm = lm or LMStudio(cfg)
    warnings: list[str] = []
    pending = lambda col: conn.execute(f"SELECT count(*) FROM cmd_texts WHERE {col} = 0").fetchone()[0]  # noqa: E731
    vi = (vi or VectorIndex(cfg, lm)) if embed else None
    if vi:
        vi.sync_model(conn)
    if not (describe and pending("described")) and not (embed and pending("embedded")):
        return 0, 0, warnings
    if not lm.available():
        return 0, 0, [f"LM Studio unreachable at {cfg.base_url}; {pending('embedded')} commands await embedding"]
    described = embedded = 0
    if describe:
        try:
            described = describe_pending(conn, lm)
        except LMStudioError as e:
            warnings.append(f"describing commands failed: {e}")
    if vi:
        try:
            embedded = vi.embed_pending(conn)
        except LMStudioError as e:
            warnings.append(str(e))
    return described, embedded, warnings


def embed_pending(cfg: Config, conn: sqlite3.Connection) -> tuple[int, str | None]:
    _d, n, warns = enrich_and_embed(cfg, conn, describe=False)
    return n, (warns[0] if warns else None)
