"""`recall` command-line interface."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from .config import load_config
from .db import connect

app = typer.Typer(help="Index your terminal commands and search them in natural language.", no_args_is_help=True, add_completion=False)
CAPTURE_DIR = Path(__file__).parent / "capture"


def _ctx():
    cfg = load_config()
    return cfg, connect(cfg.db_path)


def _err(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.YELLOW, err=True)


@app.command()
def init(shell: Annotated[str, typer.Argument(help="Shell to emit the hook for")] = "bash") -> None:
    """Print the line to add to your shell rc file."""
    if shell != "bash":
        raise typer.BadParameter("only bash is supported for now")
    typer.echo(f'# recall: add to ~/.bashrc\nsource "{CAPTURE_DIR / "recall.bash"}"')


@app.command()
def ingest(
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    embed: Annotated[bool, typer.Option(help="Embed new commands via LM Studio")] = True,
    describe: Annotated[Optional[bool], typer.Option(help="Describe new commands with the chat model (default: config)")] = None,
    wait: Annotated[bool, typer.Option(help="Wait if another ingest is running")] = True,
) -> None:
    """Move captured commands from the spool into the index."""
    from .ingest import ingest as run_ingest

    cfg, conn = _ctx()
    res = run_ingest(cfg, conn, embed=embed, describe=describe, wait=wait)
    if not quiet:
        typer.echo(f"stored {res.stored}, skipped {res.skipped}, described {res.described}, embedded {res.embedded}")
        for w in res.warnings:
            _err(w)


@app.command("import")
def import_(
    path: Annotated[Path, typer.Argument(help="History file")] = Path("~/.bash_history"),
    embed: Annotated[bool, typer.Option(help="Describe + embed imported commands via LM Studio")] = True,
) -> None:
    """Import an existing bash history file (idempotent)."""
    from .importers import import_bash_history
    from .ingest import ingest as run_ingest

    cfg, conn = _ctx()
    path = path.expanduser()
    if not path.exists():
        raise typer.BadParameter(f"{path} does not exist")
    n, skipped = import_bash_history(conn, path, [re.compile(p) for p in cfg.ignore_patterns])
    typer.echo(f"imported {n} commands from {path} ({skipped} skipped/duplicates)")
    if embed:
        typer.echo("describing + embedding (first run over a large history can take a few minutes)…")
        res = run_ingest(cfg, conn)
        typer.echo(f"described {res.described}, embedded {res.embedded}")
        for w in res.warnings:
            _err(w)


@app.command()
def search(
    query: Annotated[list[str], typer.Argument(help="What you're looking for, in plain words")],
    limit: Annotated[int, typer.Option("--limit", "-n")] = 10,
    smart: Annotated[bool, typer.Option("--smart", "-s", help="Use the LM Studio chat model to expand + rerank")] = False,
    failed: Annotated[Optional[bool], typer.Option("--failed/--succeeded", help="Filter by exit status")] = None,
    repo: Annotated[Optional[str], typer.Option(help="Only commands run in this git repo")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show how the query was interpreted")] = False,
) -> None:
    """Search your command history."""
    from .fmt import ago, short_path
    from .query_parse import Filters
    from .search import Searcher

    cfg, conn = _ctx()
    resp = Searcher(cfg, conn).search(" ".join(query), limit=limit, smart=smart, filters=Filters(failed=failed, repo=repo))
    if as_json:
        typer.echo(json.dumps(resp.to_dict(), indent=2))
        return
    for w in resp.warnings:
        _err(w)
    if verbose:
        typer.secho(f"mode={resp.mode} text={resp.text!r} filters={', '.join(resp.notes) or 'none'}", dim=True, err=True)
    if not resp.results:
        typer.echo("no matches")
        raise typer.Exit(1)
    for i, r in enumerate(resp.results, 1):
        status = typer.style("✗", fg="red") if r.last_exit not in (0, None) else typer.style("✓", fg="green")
        meta = " · ".join(x for x in [ago(r.last_ts), short_path(r.last_cwd) or (r.git_repo or ""), f"{r.runs}×" if r.runs > 1 else ""] if x)
        typer.echo(f"{typer.style(f'{i:>2}', dim=True)} {status} {typer.style(r.cmd, bold=True)}")
        typer.secho(f"      {meta}", dim=True)


@app.command()
def explain(command: Annotated[list[str], typer.Argument()]) -> None:
    """Explain a command using the LM Studio chat model."""
    from .lmstudio import LMStudioError
    from .search import Searcher

    cfg, conn = _ctx()
    try:
        typer.echo(Searcher(cfg, conn).explain(" ".join(command)))
    except LMStudioError as e:
        _err(str(e))
        raise typer.Exit(1)


@app.command()
def tui(query: Annotated[str, typer.Option("--query", "-q")] = "") -> None:
    """Interactive picker. Prints the chosen command on stdout (used by the Ctrl-G binding)."""
    from .tui.app import run

    cfg, conn = _ctx()
    chosen = run(cfg, conn, query)
    if chosen:
        sys.stdout.write(chosen)
        sys.stdout.flush()


@app.command()
def web(
    host: Annotated[Optional[str], typer.Option(help="Bind address (default: config, 127.0.0.1)")] = None,
    port: Annotated[Optional[int], typer.Option()] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = False,
) -> None:
    """Serve the web UI (on 127.0.0.1 unless --host / RECALL_WEB_HOST says otherwise)."""
    import uvicorn

    from .web.api import create_app

    cfg, _ = _ctx()
    host = host or cfg.web_host
    port = port or cfg.web_port
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    typer.echo(f"recall web UI → {url}")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


@app.command()
def reindex() -> None:
    """Drop the vector index and re-embed everything (e.g. after changing RECALL_EMBED_MODEL)."""
    from .lmstudio import LMStudio
    from .vectors import VectorIndex

    cfg, conn = _ctx()
    lm = LMStudio(cfg)
    vi = VectorIndex(cfg, lm)
    vi.reset(conn)
    n = vi.embed_pending(conn)
    typer.echo(f"re-embedded {n} commands with {cfg.embed_model}")


@app.command()
def stats() -> None:
    """Summary of what's indexed."""
    from .search import Searcher

    cfg, conn = _ctx()
    s = Searcher(cfg, conn).stats()
    typer.echo(f"{s['total_runs']} runs · {s['unique_commands']} unique · {s['failures']} failed · "
               f"{s['pending_descriptions']} awaiting description · {s['pending_embeddings']} awaiting embedding")
    typer.echo("top programs: " + ", ".join(f"{p['name']} ({p['count']})" for p in s["top_programs"][:10]))


@app.command()
def doctor() -> None:
    """Check configuration and LM Studio connectivity."""
    from .lmstudio import LMStudio, LMStudioError

    cfg, conn = _ctx()
    typer.echo(f"data dir     {cfg.data_dir}")
    typer.echo(f"LM Studio    {cfg.base_url}")
    lm = LMStudio(cfg)
    try:
        models = lm.models()
        typer.echo(f"models       {', '.join(models)}")
        ok = cfg.embed_model in models
        typer.secho(f"embed model  {cfg.embed_model} {'✓' if ok else '✗ not loaded'}", fg=None if ok else "red")
        dim = len(lm.embed(["test"])[0])
        typer.echo(f"embed dim    {dim}")
        typer.echo(f"chat model   {lm.chat_model}{'' if cfg.llm_enabled else ' (disabled)'}")
        if cfg.llm_enabled:
            import time as _t
            t0 = _t.monotonic()
            reply = lm.chat("Reply with exactly: ok", "ping", max_tokens=50)
            typer.echo(f"chat check   {reply[:20]!r} in {_t.monotonic() - t0:.1f}s (reasoning_effort={cfg.reasoning_effort or 'omitted'})")
    except LMStudioError as e:
        typer.secho(str(e), fg="red")
    hook = cfg.spool_path.exists() or conn.execute("SELECT 1 FROM commands WHERE source='hook' LIMIT 1").fetchone()
    typer.echo(f"shell hook   {'capturing' if hook else 'no commands captured yet — run `recall init bash`'}")


if __name__ == "__main__":
    app()
