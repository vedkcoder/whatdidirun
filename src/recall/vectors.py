"""ChromaDB vector index over unique command texts, embedded by the configured embedding provider."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading

from .config import Config
from .db import get_meta, set_meta
from .llm import LLMClient

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
logging.getLogger("chromadb").setLevel(logging.ERROR)


def _collection_name(model: str) -> str:
    # One collection per embedding model, so switching models never mixes vector spaces.
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", model).strip("-")[:50]
    return f"cmds-{slug}"


_clients: dict[str, object] = {}
_clients_lock = threading.Lock()


def _client(path: str):
    """One PersistentClient per path per process. Chroma's client init is not thread-safe
    (the web server's request threads and background indexer would race), so serialize it."""
    with _clients_lock:
        if path not in _clients:
            import chromadb  # slow import (~2s); keep it off the CLI's hot path
            from chromadb.config import Settings

            _clients[path] = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
        return _clients[path]


class VectorIndex:
    def __init__(self, cfg: Config, lm: LLMClient):
        self.cfg = cfg
        self.lm = lm
        # "<provider>:<model>" (bare model name for LM Studio, so 0.1 indexes are reused).
        self.embed_id = lm.embed_id
        self.client = _client(str(cfg.chroma_path))
        self.collection = self.client.get_or_create_collection(
            _collection_name(self.embed_id), metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    def sync_model(self, conn: sqlite3.Connection) -> bool:
        """If the embedding provider/model changed since last run, mark everything for re-embedding."""
        if get_meta(conn, "embed_model") != self.embed_id:
            conn.execute("UPDATE cmd_texts SET embedded = 0")
            set_meta(conn, "embed_model", self.embed_id)
            set_meta(conn, "embed_dims", "")
            conn.commit()
            return True
        return False

    def _check_dims(self, conn: sqlite3.Connection, vectors: list[list[float]]) -> None:
        """Same model name, different vector size (e.g. a server swapped the model): start the index over."""
        if not vectors:
            return
        dims = str(len(vectors[0]))
        known = get_meta(conn, "embed_dims")
        if known and known != dims:
            self.reset(conn)
        if known != dims:
            set_meta(conn, "embed_dims", dims)
            conn.commit()

    def embed_pending(self, conn: sqlite3.Connection, batch_size: int = 256) -> int:
        self.sync_model(conn)
        done = 0
        while rows := conn.execute(
            "SELECT text_id, embed_text, git_repo FROM cmd_texts WHERE embedded = 0 LIMIT ?", (batch_size,)
        ).fetchall():
            vectors = self.lm.embed([r["embed_text"] for r in rows], kind="document")
            if get_meta(conn, "embed_dims") != (str(len(vectors[0])) if vectors else None):
                self._check_dims(conn, vectors)
            self.collection.upsert(
                ids=[str(r["text_id"]) for r in rows],
                embeddings=vectors,
                documents=[r["embed_text"] for r in rows],
                metadatas=[{"git_repo": r["git_repo"] or ""} for r in rows],
            )
            conn.executemany("UPDATE cmd_texts SET embedded = 1 WHERE text_id = ?", [(r["text_id"],) for r in rows])
            conn.commit()
            done += len(rows)
        return done

    def query(self, text: str, n: int = 100) -> list[tuple[int, float]]:
        """Return [(text_id, cosine_distance)] nearest first."""
        count = self.collection.count()
        if count == 0:
            return []
        [vec] = self.lm.embed([text], kind="query")
        res = self.collection.query(query_embeddings=[vec], n_results=min(n, count), include=["distances"])
        return [(int(i), d) for i, d in zip(res["ids"][0], res["distances"][0])]

    def reset(self, conn: sqlite3.Connection) -> None:
        self.client.delete_collection(self.collection.name)
        self.collection = self.client.get_or_create_collection(
            self.collection.name, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )
        conn.execute("UPDATE cmd_texts SET embedded = 0")
        conn.commit()
