"""Hybrid search: BM25 (SQLite FTS5) + vectors (ChromaDB), fused with Reciprocal Rank Fusion."""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import asdict, dataclass, field

from .config import Config
from .query_parse import Filters, ParsedQuery, keywords, parse_query

RRF_K = 60
POOL = 200  # candidates per retriever


@dataclass
class Result:
    text_id: int
    cmd: str
    git_repo: str | None
    description: str | None
    runs: int
    failures: int
    last_ts: float | None
    last_cwd: str | None
    last_exit: int | None
    last_branch: str | None
    score: float = 0.0
    sources: list[str] = field(default_factory=list)  # which retrievers matched: bm25 / vector / llm


@dataclass
class SearchResponse:
    query: str
    text: str
    filters: dict
    notes: list[str]
    mode: str  # hybrid | keyword | semantic | recent
    results: list[Result]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def rrf(rankings: dict[str, list[int]], k: int = RRF_K) -> tuple[dict[int, float], dict[int, list[str]]]:
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}
    for name, ids in rankings.items():
        for rank, tid in enumerate(ids):
            scores[tid] = scores.get(tid, 0.0) + 1.0 / (k + rank + 1)
            sources.setdefault(tid, []).append(name)
    return scores, sources


def boost(score: float, last_ts: float | None, runs: int, now: float) -> float:
    recency = math.exp(-((now - last_ts) / 86400) / 30) if last_ts else 0.0  # ~30-day decay
    freq = min(math.log1p(runs) / math.log1p(100), 1.0)
    return score * (1 + 0.25 * recency + 0.15 * freq)


class Searcher:
    def __init__(self, cfg: Config, conn: sqlite3.Connection):
        self.cfg = cfg
        self.conn = conn
        self._lm = None
        self._vi = None
        self._lm_ok: tuple[float, bool] = (0.0, False)

    # ---- lazy LM Studio / Chroma ---------------------------------------------------
    @property
    def lm(self):
        if self._lm is None:
            from .lmstudio import LMStudio
            self._lm = LMStudio(self.cfg)
        return self._lm

    @property
    def vectors(self):
        if self._vi is None:
            from .vectors import VectorIndex
            self._vi = VectorIndex(self.cfg, self.lm)
        return self._vi

    def lm_available(self, ttl: float = 30.0) -> bool:
        checked, ok = self._lm_ok
        if time.monotonic() - checked > ttl:
            self._lm_ok = (time.monotonic(), self.lm.available())
        return self._lm_ok[1]

    @property
    def vectors_ready(self) -> bool:
        return self._vi is not None

    def warm(self) -> None:
        """Load ChromaDB (~2-3s import) ahead of time, e.g. from a background thread."""
        try:
            self.vectors
        except Exception:  # noqa: BLE001
            pass

    def refresh(self, embed: bool = True) -> list[str]:
        """Pull new commands from the spool and embed them. Describing (slow, LLM) is left to background ingest."""
        from .ingest import ingest

        ok = embed and self.lm_available()
        res = ingest(self.cfg, self.conn, embed=ok, describe=False, wait=False, lm=self.lm, vi=self.vectors if ok else None)
        return [w for w in res.warnings if "another ingest" not in w]

    # ---- retrievers ------------------------------------------------------------------
    def _allowed(self, f: Filters) -> set[int] | None:
        if not f.any():
            return None
        where, args = self._where(f)
        return {r[0] for r in self.conn.execute(f"SELECT DISTINCT text_id FROM commands WHERE {where}", args)}

    @staticmethod
    def _where(f: Filters) -> tuple[str, list]:
        clauses, args = ["1=1"], []
        if f.since is not None:
            clauses.append("ts >= ?"); args.append(f.since)
        if f.until is not None:
            clauses.append("ts < ?"); args.append(f.until)
        if f.failed is True:
            clauses.append("exit_code != 0")
        elif f.failed is False:
            clauses.append("exit_code = 0")
        if f.repo:
            clauses.append("git_repo = ? COLLATE NOCASE"); args.append(f.repo)
        if f.cwd:
            clauses.append("(cwd LIKE ? OR cwd LIKE ?)"); args += [f"%/{f.cwd}", f"%/{f.cwd}/%"]
        return " AND ".join(clauses), args

    def bm25(self, words: list[str], n: int = POOL) -> list[int]:
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        rows = self.conn.execute(
            "SELECT rowid FROM cmd_fts WHERE cmd_fts MATCH ? ORDER BY bm25(cmd_fts, 1.0, 0.6, 0.3) LIMIT ?", (match, n)
        )
        return [r[0] for r in rows]

    def vector(self, text: str, n: int = POOL) -> list[int]:
        return [tid for tid, _dist in self.vectors.query(text, n)]

    def recent(self, f: Filters, n: int) -> list[int]:
        where, args = self._where(f)
        rows = self.conn.execute(
            f"SELECT text_id FROM commands WHERE {where} GROUP BY text_id ORDER BY max(coalesce(ts, 0)) DESC, max(id) DESC LIMIT ?",
            [*args, n],
        )
        return [r[0] for r in rows]

    def fetch(self, ids: list[int]) -> dict[int, Result]:
        if not ids:
            return {}
        out: dict[int, Result] = {}
        for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):
            q = f"""
                SELECT t.text_id, t.cmd, t.git_repo, t.description, count(c.id) AS runs,
                       sum(CASE WHEN c.exit_code != 0 THEN 1 ELSE 0 END) AS failures, max(c.ts) AS last_ts,
                       (SELECT cwd FROM commands WHERE text_id = t.text_id ORDER BY ts DESC NULLS LAST, id DESC LIMIT 1) AS last_cwd,
                       (SELECT exit_code FROM commands WHERE text_id = t.text_id ORDER BY ts DESC NULLS LAST, id DESC LIMIT 1) AS last_exit,
                       (SELECT git_branch FROM commands WHERE text_id = t.text_id ORDER BY ts DESC NULLS LAST, id DESC LIMIT 1) AS last_branch
                FROM cmd_texts t JOIN commands c ON c.text_id = t.text_id
                WHERE t.text_id IN ({','.join('?' * len(chunk))})
                GROUP BY t.text_id"""
            for r in self.conn.execute(q, chunk):
                out[r["text_id"]] = Result(**{k: r[k] for k in r.keys()})
        return out

    def known_locations(self) -> tuple[set[str], set[str]]:
        repos = {r[0] for r in self.conn.execute("SELECT DISTINCT git_repo FROM commands WHERE git_repo IS NOT NULL")}
        dirs = set()
        for (cwd,) in self.conn.execute("SELECT DISTINCT cwd FROM commands WHERE cwd IS NOT NULL"):
            dirs.add(cwd.rstrip("/").rsplit("/", 1)[-1])
        return repos, dirs - {""}

    # ---- main entry ----------------------------------------------------------------------
    def search(
        self, query: str, limit: int = 20, smart: bool = False, filters: Filters | None = None,
        refresh: bool = True, wait_vectors: bool = True,
    ) -> SearchResponse:
        """wait_vectors=False: if ChromaDB isn't loaded yet, answer with BM25 only instead of blocking."""
        use_vectors = wait_vectors or self.vectors_ready
        warnings = self.refresh(embed=use_vectors) if refresh else []
        parsed: ParsedQuery = parse_query(query, *self.known_locations())
        f = parsed.filters
        if filters:  # explicit UI/CLI filters win over parsed ones
            for k, v in vars(filters).items():
                if v is not None:
                    setattr(f, k, v)

        text = parsed.text
        words = keywords(text)
        if smart and text:
            words, text, warnings = self._expand(text, words, warnings)

        if not text:
            ids = self.recent(f, limit)
            res = self.fetch(ids)
            results = [res[i] for i in ids if i in res]
            return SearchResponse(query, "", asdict(f), parsed.notes, "recent", results, warnings)

        rankings: dict[str, list[int]] = {}
        allowed = self._allowed(f)
        pool = POOL * (3 if allowed is not None else 1)
        rankings["bm25"] = self.bm25(words, pool)
        if not use_vectors:
            pass  # caller will re-query once the vector index has loaded
        elif self.lm_available():
            from .lmstudio import LMStudioError
            try:
                rankings["vector"] = self.vector(text, pool)
            except LMStudioError as e:
                warnings.append(f"semantic search unavailable ({e}); keyword results only")
        else:
            warnings.append(f"LM Studio unreachable at {self.cfg.base_url}; keyword results only")
        if allowed is not None:
            rankings = {k: [i for i in v if i in allowed] for k, v in rankings.items()}

        scores, sources = rrf(rankings)
        res = self.fetch(list(scores))
        now = time.time()
        for tid, r in res.items():
            r.score = boost(scores[tid], r.last_ts, r.runs, now)
            r.sources = sources[tid]
        results = sorted(res.values(), key=lambda r: r.score, reverse=True)[: max(limit, 25 if smart else limit)]

        if smart and results:
            results, warnings = self._rerank(query, results, warnings)
        mode = "hybrid" if "vector" in rankings else "keyword"
        return SearchResponse(query, text, asdict(f), parsed.notes, mode, results[:limit], warnings)

    # ---- LLM helpers ------------------------------------------------------------------------
    def _expand(self, text: str, words: list[str], warnings: list[str]):
        from .lmstudio import LMStudioError
        try:
            data = self.lm.chat_json(
                "You turn a natural-language description of a shell command into search keywords. "
                'Reply with JSON only: {"keywords": [likely program names, subcommands and flags], "rewrite": "one-line description"}',
                text,
                max_tokens=2000,
            )
            extra = [w for k in data.get("keywords", []) for w in keywords(str(k))]
            words = list(dict.fromkeys(words + extra))
            text = f"{text} {data.get('rewrite', '')}".strip()
        except LMStudioError as e:
            warnings.append(f"query expansion skipped: {e}")
        return words, text, warnings

    def _rerank(self, query: str, results: list[Result], warnings: list[str]):
        from .lmstudio import LMStudioError
        listing = "\n".join(f"{i}: {r.cmd}" for i, r in enumerate(results))
        try:
            data = self.lm.chat_json(
                "You rank shell commands by how well they match what the user is looking for. "
                'Reply with JSON only: {"order": [indices of matching commands, best first]}. Omit irrelevant ones.',
                f"Looking for: {query}\n\nCommands:\n{listing}",
                max_tokens=2000,
            )
            order = [int(i) for i in data.get("order", []) if str(i).isdigit() and int(i) < len(results)]
            order = list(dict.fromkeys(order))
            if order:
                picked = [results[i] for i in order]
                for r in picked:
                    r.sources = [*r.sources, "llm"]
                rest = [r for i, r in enumerate(results) if i not in set(order)]
                return picked + rest, warnings
        except LMStudioError as e:
            warnings.append(f"rerank skipped: {e}")
        return results, warnings

    def explain(self, cmd: str) -> str:
        return self.lm.chat(
            "You explain shell commands to a developer. Be concise: one sentence summary, then a short bullet per "
            "notable part/flag. Mention anything destructive. Plain text or simple markdown.",
            cmd,
            max_tokens=2000,
        )

    # ---- detail / stats -----------------------------------------------------------------------
    def runs(self, text_id: int, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, duration_ms, exit_code, cwd, hostname, git_repo, git_branch, source FROM commands "
            "WHERE text_id = ? ORDER BY ts DESC NULLS LAST, id DESC LIMIT ?",
            (text_id, limit),
        )
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        c = self.conn
        total, unique = c.execute("SELECT count(*), count(DISTINCT text_id) FROM commands").fetchone()
        failures = c.execute("SELECT count(*) FROM commands WHERE exit_code != 0").fetchone()[0]
        pending = c.execute("SELECT count(*) FROM cmd_texts WHERE embedded = 0").fetchone()[0]
        undescribed = c.execute("SELECT count(*) FROM cmd_texts WHERE described = 0").fetchone()[0]
        programs: dict[str, int] = {}
        for (cmd,) in c.execute("SELECT cmd FROM commands"):
            tok = cmd.split(maxsplit=1)[0] if cmd.split() else ""
            if tok in {"sudo", "time", "nohup", "env"} and len(cmd.split()) > 1:
                tok = cmd.split()[1]
            programs[tok] = programs.get(tok, 0) + 1
        top_programs = sorted(programs.items(), key=lambda kv: -kv[1])[:15]
        top_repos = c.execute(
            "SELECT git_repo, count(*) n FROM commands WHERE git_repo IS NOT NULL GROUP BY git_repo ORDER BY n DESC LIMIT 10"
        ).fetchall()
        since = time.time() - 30 * 86400
        daily = c.execute(
            "SELECT date(ts, 'unixepoch', 'localtime') d, count(*) n, sum(exit_code != 0) f FROM commands "
            "WHERE ts >= ? GROUP BY d ORDER BY d",
            (since,),
        ).fetchall()
        top_failing = c.execute(
            "SELECT cmd, count(*) n FROM commands WHERE exit_code != 0 GROUP BY text_id ORDER BY n DESC LIMIT 10"
        ).fetchall()
        return {
            "total_runs": total,
            "unique_commands": unique,
            "failures": failures,
            "pending_embeddings": pending,
            "pending_descriptions": undescribed,
            "top_programs": [{"name": k, "count": v} for k, v in top_programs],
            "top_repos": [{"name": r[0], "count": r[1]} for r in top_repos],
            "daily": [{"date": r[0], "runs": r[1], "failures": r[2] or 0} for r in daily],
            "top_failing": [{"cmd": r[0], "count": r[1]} for r in top_failing],
        }
