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
    embed: Annotated[bool, typer.Option(help="Embed new commands with the embedding provider")] = True,
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
    embed: Annotated[bool, typer.Option(help="Describe + embed imported commands with the configured providers")] = True,
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
    smart: Annotated[bool, typer.Option("--smart", "-s", help="Use the chat model to expand + rerank")] = False,
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
    """Explain a command using the configured chat model."""
    from .llm import LLMError
    from .search import Searcher

    cfg, conn = _ctx()
    try:
        typer.echo(Searcher(cfg, conn).explain(" ".join(command)))
    except LLMError as e:
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
    host = cfg.web_host = host or cfg.web_host  # the settings API needs to know if it's reachable from the network
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
    """Drop the vector index and re-embed everything with the current embedding provider and model."""
    from .llm import LLMClient, LLMError
    from .vectors import VectorIndex

    cfg, conn = _ctx()
    lm = LLMClient(cfg)
    try:
        vi = VectorIndex(cfg, lm)
        vi.reset(conn)
        n = vi.embed_pending(conn)
    except LLMError as e:
        _err(str(e))
        raise typer.Exit(1)
    typer.echo(f"re-embedded {n} commands with {lm.embed_spec.label} · {lm.embed_model}")


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
def providers() -> None:
    """List the chat and embedding providers recall supports, and which ones are active."""
    from .providers import resolve as R
    from .providers.catalog import all_providers
    from .settings import store_for

    cfg, _ = _ctx()
    st = store_for(cfg)
    chat, emb = R.chat_provider(cfg, st).value, R.embed_provider(cfg, st).value
    typer.echo(f"{'provider':<12} {'name':<28} {'chat':<6} {'embed':<6} key")
    for p in all_providers():
        key = R.api_key(cfg, p.id, st)
        if not p.needs_key:
            key_state = "not needed"
        elif key.value:
            key_state = f"set ({key.source})"
        else:
            key_state = typer.style("missing", fg="yellow") + (f" — {p.key_env[0]} or {p.key_url}" if p.key_env else "")
        mark = lambda on, active: (typer.style("active", fg="green") if active else "yes") if on else "-"  # noqa: E731
        typer.echo(f"{p.id:<12} {p.label:<28} {mark(p.chat, p.id == chat):<6} {mark(p.embeddings, p.id == emb):<6} {key_state}")
    typer.echo("\nChoose in the web UI's Settings, with `recall config set chat_provider <id>`, or RECALL_CHAT_PROVIDER / RECALL_EMBED_PROVIDER.")
    typer.echo("Add a key with `recall keys set <provider>`.")


config_app = typer.Typer(help="Show or change saved settings (the same ones as the web UI's Settings page).", no_args_is_help=True)
app.add_typer(config_app, name="config")

_SHOWN = ["chat_provider", "chat_model", "embed_provider", "embed_model", "llm_enabled", "describe", "reasoning_effort", "llm_timeout"]


@config_app.command("show")
def config_show() -> None:
    """Show each setting, its value and where it comes from (env, settings, file, default)."""
    from .providers import resolve as R
    from .settings import store_for

    cfg, _ = _ctx()
    st = store_for(cfg)
    for key in _SHOWN:
        v = R.setting(cfg, st, key)
        typer.echo(f"{key:<17} {v.value or '(auto)':<40} {typer.style(v.source, dim=True)}")
    for key, value in sorted(st.all().items()):
        if key.startswith("provider."):
            typer.echo(f"{key:<17} {value:<40} {typer.style('settings', dim=True)}")
    for e in st.custom():
        typer.echo(f"custom endpoint   {e['id']}  {e['label']}  {e['base_url']}")


@config_app.command("set")
def config_set(key: Annotated[str, typer.Argument()], value: Annotated[str, typer.Argument()]) -> None:
    """Save a setting, e.g. `recall config set chat_provider anthropic` or `provider.ollama.base_url http://box:11434`."""
    from .settings import SettingsError, store_for

    cfg, _ = _ctx()
    try:
        store_for(cfg).set(key, value)
    except SettingsError as e:
        _err(str(e))
        raise typer.Exit(1)
    if key in cfg.env_overrides:
        _err(f"saved, but the environment variable for {key} overrides it")
    typer.echo(f"{key} = {value}")


@config_app.command("unset")
def config_unset(key: Annotated[str, typer.Argument()]) -> None:
    """Remove a saved setting (falls back to config.toml or the default)."""
    from .settings import SettingsError, store_for

    cfg, _ = _ctx()
    try:
        store_for(cfg).set(key, None)
    except SettingsError as e:
        _err(str(e))
        raise typer.Exit(1)
    typer.echo(f"{key} unset")


keys_app = typer.Typer(help="Manage API keys (stored encrypted in the database).", no_args_is_help=True)
app.add_typer(keys_app, name="keys")


@keys_app.command("set")
def keys_set(provider: Annotated[str, typer.Argument(help="Provider id, e.g. openai (see `recall providers`)")]) -> None:
    """Save an API key. You're prompted for it, so it doesn't end up in your shell history."""
    from .settings import SettingsError, store_for

    cfg, _ = _ctx()
    key = typer.prompt(f"API key for {provider}", hide_input=True)
    try:
        store_for(cfg).set_secret(provider, key)
    except SettingsError as e:
        _err(str(e))
        raise typer.Exit(1)
    typer.echo(f"saved (encrypted) key for {provider}")


@keys_app.command("remove")
def keys_remove(provider: Annotated[str, typer.Argument()]) -> None:
    """Delete a saved API key."""
    from .settings import store_for

    cfg, _ = _ctx()
    typer.echo("removed" if store_for(cfg).delete_secret(provider) else f"no saved key for {provider}")


@app.command()
def doctor() -> None:
    """Check configuration and connectivity of the chat and embedding providers."""
    import time as _t

    from .llm import LLMClient, LLMError
    from .providers import AuthError, UnavailableError

    cfg, conn = _ctx()
    typer.echo(f"data dir     {cfg.data_dir}")
    lm = LLMClient(cfg)

    try:
        es = lm.embed_spec
        typer.echo(f"embeddings   {es.label} · {es.base_url} · {es.model}")
        try:
            models = lm.embed_models()
            if es.model in models:
                typer.echo("embed model  ✓ available")
            else:
                typer.secho(f"embed model  ? not listed by {es.label} ({len(models)} models listed)", fg="yellow")
        except (AuthError, UnavailableError):
            raise
        except LLMError as e:  # some servers have no /models endpoint; the embed below is the real test
            typer.secho(f"embed models {e}", fg="yellow")
        dim = len(lm.embed(["test"])[0])
        typer.echo(f"embed dim    {dim}")
    except LLMError as e:
        typer.secho(f"embeddings   {e}", fg="red")

    try:
        cs = lm.chat_spec
        typer.echo(f"chat         {cs.label} · {cs.base_url}")
        typer.echo(f"chat model   {lm.chat_model}{'' if lm.llm_enabled else ' (AI features off)'}")
        if lm.llm_enabled:
            t0 = _t.monotonic()
            reply = lm.chat("Reply with exactly: ok", "ping", max_tokens=50)
            effort = cs.reasoning_effort or "omitted"
            typer.echo(f"chat check   {reply[:20]!r} in {_t.monotonic() - t0:.1f}s (reasoning_effort={effort})")
    except LLMError as e:
        typer.secho(f"chat         {e}", fg="red")
    hook = cfg.spool_path.exists() or conn.execute("SELECT 1 FROM commands WHERE source='hook' LIMIT 1").fetchone()
    from .config import IS_WINDOWS

    hint = "run `recall init powershell --install`" if IS_WINDOWS else "run `recall init bash`"
    typer.echo(f"shell hook   {'capturing' if hook else f'no commands captured yet: {hint}'}")


if __name__ == "__main__":
    app()
