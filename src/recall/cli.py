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

def _safe_streams() -> None:
    # Windows consoles/pipes often use a legacy code page; never crash on ✓/✗ or non-ASCII commands.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


_safe_streams()

app = typer.Typer(help="Index your terminal commands and search them in natural language.", no_args_is_help=True, add_completion=False)


def _ctx():
    cfg = load_config()
    return cfg, connect(cfg.db_path)


def _err(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.YELLOW, err=True)


@app.command()
def init(
    shell: Annotated[str, typer.Argument(help="bash or powershell")] = "bash",
    install: Annotated[bool, typer.Option("--install", help="PowerShell: add the hook to your profile(s)")] = False,
    uninstall: Annotated[bool, typer.Option("--uninstall", help="PowerShell: remove the hook from your profile(s)")] = False,
) -> None:
    """Print the line to add to your shell startup file (or, for PowerShell, install it)."""
    from . import shells

    shell = {"pwsh": "powershell"}.get(shell.lower(), shell.lower())
    if shell == "bash":
        if install or uninstall:
            raise typer.BadParameter("--install/--uninstall are for powershell; add the line below to ~/.bashrc")
        typer.echo(f"# recall: add to ~/.bashrc\n{shells.bash_line()}")
        return
    if shell != "powershell":
        raise typer.BadParameter("supported shells: bash, powershell")
    if not (install or uninstall):
        typer.echo(f"# recall: add to your PowerShell profile ($PROFILE)\n{shells.powershell_line()}")
        return
    for profile in shells.powershell_profiles():
        changed = shells.uninstall_profile(profile) if uninstall else shells.install_profile(profile, shells.powershell_line())
        typer.echo(f"{'updated' if changed else 'unchanged'}  {profile}")


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
    path: Annotated[Optional[Path], typer.Argument(help="History file (default: ~/.bash_history, or PSReadLine history on Windows)")] = None,
    fmt: Annotated[str, typer.Option("--format", help="auto, bash or psreadline")] = "auto",
    embed: Annotated[bool, typer.Option(help="Describe + embed imported commands via LM Studio")] = True,
) -> None:
    """Import an existing bash or PowerShell (PSReadLine) history file (idempotent)."""
    from .config import IS_WINDOWS
    from .importers import import_history, psreadline_history_path
    from .ingest import ingest as run_ingest

    cfg, conn = _ctx()
    if path is None:
        path = psreadline_history_path() if IS_WINDOWS else Path("~/.bash_history")
    path = path.expanduser()
    if not path.exists():
        raise typer.BadParameter(f"{path} does not exist")
    try:
        n, skipped = import_history(conn, path, [re.compile(p) for p in cfg.ignore_patterns], fmt)
    except ValueError as e:
        raise typer.BadParameter(str(e))
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
def tui(
    query: Annotated[str, typer.Option("--query", "-q", envvar="RECALL_TUI_QUERY", show_envvar=False)] = "",
    out: Annotated[Optional[Path], typer.Option("--out", help="Write the chosen command to this file instead of stdout")] = None,
) -> None:
    """Interactive picker. Prints the chosen command on stdout, or writes it to --out (used by the Ctrl-G binding).

    On Windows the picker draws on stdout, so the PowerShell binding can't capture stdout and uses --out instead.
    """
    from .tui.app import run

    cfg, conn = _ctx()
    chosen = run(cfg, conn, query)
    if out is not None:
        out.write_text(chosen or "", encoding="utf-8")
    elif chosen:
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
def tray() -> None:
    """Run in the system tray: web UI + background indexer (Windows app; needs the `tray` extra)."""
    try:
        from .tray import run
    except ImportError as e:
        _err(f"tray support isn't installed ({e.name}); install with: uv tool install 'whatdidirun[tray]'")
        raise typer.Exit(1)
    run(load_config())


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
    from .config import IS_WINDOWS

    hint = "run `recall init powershell --install`" if IS_WINDOWS else "run `recall init bash`"
    typer.echo(f"shell hook   {'capturing' if hook else f'no commands captured yet: {hint}'}")


if __name__ == "__main__":
    app()
