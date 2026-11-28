"""Generate short plain-English descriptions of commands with the configured chat model.

Embedding models map "df -h" and "free disk space" far apart; a description like
"show free and used disk space on mounted filesystems" bridges that gap for both the
vector index and BM25.
"""

from __future__ import annotations

import sqlite3

from .db import embed_text_for
from .llm import LLMClient, LLMError

SYSTEM = (
    "For each numbered shell command, write one short plain-English description (max 20 words) of what it does, "
    "using the everyday words someone would search with (e.g. disk space, memory, network, archive, permissions, "
    "container, git branch). Do not repeat the command. "
    'Reply with JSON only: {"d": ["description 0", "description 1", ...]} with exactly one entry per command, in order.'
)


def describe_batch(lm: LLMClient, cmds: list[str]) -> list[str]:
    listing = "\n".join(f"{i}: {c[:400]}" for i, c in enumerate(cmds))
    data = lm.chat_json(SYSTEM, listing, max_tokens=60 * len(cmds) + 200)
    out = data.get("d") if isinstance(data, dict) else data
    if not isinstance(out, list) or len(out) != len(cmds):
        raise LLMError(f"expected {len(cmds)} descriptions, got {len(out) if isinstance(out, list) else type(out).__name__}")
    return [str(x).strip()[:300] for x in out]


def describe_pending(conn: sqlite3.Connection, lm: LLMClient, batch_size: int = 20, max_batches: int | None = None) -> int:
    """Describe texts with described=0. Marks them for re-embedding. Returns number described."""
    done = batches = 0
    while rows := conn.execute(
        "SELECT text_id, cmd, git_repo FROM cmd_texts WHERE described = 0 ORDER BY text_id DESC LIMIT ?", (batch_size,)
    ).fetchall():
        try:
            descs = describe_batch(lm, [r["cmd"] for r in rows])
        except LLMError:
            if len(rows) == 1:  # can't split further; give up on this one so we don't loop forever
                conn.execute("UPDATE cmd_texts SET described = -1 WHERE text_id = ?", (rows[0]["text_id"],))
                conn.commit()
                continue
            # A long batch can confuse the model; retry each half on its own.
            mid = len(rows) // 2
            descs = []
            for half in (rows[:mid], rows[mid:]):
                try:
                    descs += describe_batch(lm, [r["cmd"] for r in half])
                except LLMError:
                    descs += [None] * len(half)
        for r, d in zip(rows, descs):
            if d is None:
                conn.execute("UPDATE cmd_texts SET described = -1 WHERE text_id = ?", (r["text_id"],))
            else:
                conn.execute(
                    "UPDATE cmd_texts SET description = ?, described = 1, embed_text = ?, embedded = 0 WHERE text_id = ?",
                    (d, embed_text_for(r["cmd"], r["git_repo"], d), r["text_id"]),
                )
                done += 1
        conn.commit()
        batches += 1
        if max_batches and batches >= max_batches:
            break
    return done
