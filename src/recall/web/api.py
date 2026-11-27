"""FastAPI backend for the React UI. Binds to localhost only (see cli.web)."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import Config
from ..db import connect
from ..lmstudio import LMStudioError
from ..query_parse import Filters
from ..search import Searcher

DIST = Path(__file__).parent / "dist"


class ExplainIn(BaseModel):
    cmd: str


def create_app(cfg: Config, background_indexer: bool = True) -> FastAPI:
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if background_indexer:
            threading.Thread(target=indexer, daemon=True, name="recall-indexer").start()
        yield
        stop.set()

    app = FastAPI(title="WhatdidIRun??", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    searcher = Searcher(cfg, connect(cfg.db_path))
    lock = threading.Lock()  # one sqlite connection shared across the threadpool

    def indexer():
        # Describe + embed new commands once a minute while the UI is running.
        from ..ingest import ingest

        conn = connect(cfg.db_path)
        while True:
            try:
                ingest(cfg, conn, wait=False)
            except Exception:  # noqa: BLE001 - never kill the thread
                pass
            if stop.wait(60):
                return

    @app.get("/api/search")
    def search(
        q: str = "",
        limit: int = Query(30, le=200),
        smart: bool = False,
        failed: bool | None = None,
        repo: str | None = None,
        cwd: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ):
        with lock:
            resp = searcher.search(q, limit=limit, smart=smart, filters=Filters(since=since, until=until, failed=failed, repo=repo, cwd=cwd))
        return resp.to_dict()

    @app.get("/api/command/{text_id}")
    def command(text_id: int):
        with lock:
            res = searcher.fetch([text_id])
            if text_id not in res:
                raise HTTPException(404, "unknown command")
            return {"command": res[text_id].__dict__, "runs": searcher.runs(text_id)}

    @app.post("/api/explain")
    def explain(body: ExplainIn):
        try:
            return {"explanation": searcher.explain(body.cmd)}
        except LMStudioError as e:
            raise HTTPException(503, str(e)) from e

    @app.get("/api/stats")
    def stats():
        with lock:
            return searcher.stats()

    @app.get("/api/facets")
    def facets():
        with lock:
            repos, _dirs = searcher.known_locations()
        return {"repos": sorted(repos, key=str.lower)}

    @app.get("/api/health")
    def health():
        return {"lmstudio": searcher.lm_available(ttl=5), "base_url": cfg.base_url, "embed_model": cfg.embed_model, "llm_enabled": cfg.llm_enabled}

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (DIST / path).resolve()
            if path and f.is_file() and DIST.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_ui():
            return {"error": "web UI not built — run `npm install && npm run build` in web/"}

    return app
