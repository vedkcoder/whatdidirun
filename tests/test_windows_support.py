"""Cross-platform pieces added for Windows/PowerShell. All run on any OS."""

from pathlib import Path

from typer.testing import CliRunner

from recall import shells
from recall.cli import app
from recall.config import default_config_path, default_data_dir
from recall.db import connect
from recall.importers import detect_format, import_history, parse_psreadline_history
from recall.ingest import _lock, ingest_spool, prepare_run


def test_windows_default_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert default_config_path(windows=True) == tmp_path / "Roaming" / "recall" / "config.toml"
    assert default_data_dir(windows=True) == tmp_path / "Local" / "recall"


def test_posix_default_dirs():
    assert default_data_dir(windows=False) == Path("~/.local/share/recall").expanduser()
    assert default_config_path(windows=False) == Path("~/.config/recall/config.toml").expanduser()


def test_lock_is_exclusive_and_reentrant_after_release(tmp_path):
    lock = tmp_path / "ingest.lock"
    with _lock(lock) as got:
        assert got
        with _lock(lock, blocking=False) as second:  # separate open file -> contended
            assert second is False
    with _lock(lock, blocking=False) as again:
        assert again


def test_parse_psreadline_history_joins_backtick_continuations():
    text = "git status\nGet-ChildItem `\n  -Recurse `\n  -Filter *.py\n\n docker ps\n"
    assert parse_psreadline_history(text) == [
        (None, "git status"),
        (None, "Get-ChildItem \n  -Recurse \n  -Filter *.py"),
        (None, " docker ps"),
    ]


def test_import_psreadline_history(cfg, tmp_path):
    conn = connect(cfg.db_path)
    h = tmp_path / "ConsoleHost_history.txt"
    h.write_text("winget upgrade --all\n secret\nwinget upgrade --all\nGet-Process | Sort CPU\n", encoding="utf-8")
    assert detect_format(h) == "psreadline"
    # leading-space line skipped; untimestamped repeat collapses
    assert import_history(conn, h, []) == (2, 2)
    assert import_history(conn, h, []) == (0, 4)
    shells_used = {r[0] for r in conn.execute("SELECT DISTINCT shell FROM commands")}
    assert shells_used == {"pwsh"}


def test_prepare_run_accepts_powershell_record():
    run = prepare_run(
        {"cmd": "Get-ChildItem C:\\Windows", "cwd": "C:\\", "exit": 0, "start": "1764200000.5", "end": "1764200001.75",
         "host": "pc", "session": "s", "shell": "pwsh"},
        [],
    )
    assert run["shell"] == "pwsh" and run["duration_ms"] == 1250 and run["exit_code"] == 0


def test_spool_ingest_roundtrip(cfg):
    cfg.data_dir.mkdir(parents=True)
    cfg.spool_path.write_text('{"cmd":"echo hi","exit":0,"start":"1","end":"2","shell":"pwsh"}\n', encoding="utf-8")
    conn = connect(cfg.db_path)
    assert ingest_spool(conn, cfg) == (1, 0)
    assert not cfg.spool_path.exists()


def test_profile_install_is_idempotent_and_uninstall_restores(tmp_path):
    profile = tmp_path / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
    profile.parent.mkdir()
    profile.write_text("Set-Alias ll Get-ChildItem\n", encoding="utf-8")
    line = '. "C:\\app\\recall.ps1"'
    assert shells.install_profile(profile, line)
    assert not shells.install_profile(profile, line)  # second run: no change
    text = profile.read_text(encoding="utf-8-sig")
    assert text.count(shells.BEGIN) == 1 and line in text and text.startswith("Set-Alias ll")
    assert shells.install_profile(profile, '. "D:\\new\\recall.ps1"')  # path changed: block refreshed in place
    assert profile.read_text(encoding="utf-8-sig").count(shells.BEGIN) == 1
    assert shells.uninstall_profile(profile)
    assert profile.read_text(encoding="utf-8-sig") == "Set-Alias ll Get-ChildItem\n"
    assert not shells.uninstall_profile(profile)


def test_profile_install_creates_missing_file(tmp_path):
    profile = tmp_path / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
    assert shells.install_profile(profile, ". x")
    assert profile.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM for Windows PowerShell 5.1


def test_windows_profile_locations(tmp_path):
    docs = tmp_path / "Docs"
    assert shells.powershell_profiles(docs) == [
        docs / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
        docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
    ]


def test_cli_init_powershell_prints_dot_source_line():
    res = CliRunner().invoke(app, ["init", "powershell"])
    assert res.exit_code == 0
    assert str(shells.PWSH_HOOK) in res.output and shells.PWSH_HOOK.exists()


def test_cli_init_rejects_unknown_shell():
    assert CliRunner().invoke(app, ["init", "fish"]).exit_code != 0


def test_cli_tui_writes_choice_to_out_file(monkeypatch, tmp_path):
    """The PowerShell Ctrl-G binding can't capture stdout on Windows (the picker draws there): it uses --out."""
    seen = {}

    def fake_run(cfg, conn, query):
        seen["query"] = query
        return "git log --oneline"

    monkeypatch.setattr("recall.tui.app.run", fake_run)
    monkeypatch.setenv("RECALL_TUI_QUERY", "show recent commits")
    out = tmp_path / "choice.txt"
    res = CliRunner().invoke(app, ["tui", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert seen["query"] == "show recent commits"
    assert out.read_text(encoding="utf-8") == "git log --oneline"
    assert res.output == ""


def test_cli_tui_cancel_leaves_out_file_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("recall.tui.app.run", lambda cfg, conn, query: None)
    out = tmp_path / "choice.txt"
    out.write_text("stale", encoding="utf-8")
    assert CliRunner().invoke(app, ["tui", "--out", str(out)]).exit_code == 0
    assert out.read_text(encoding="utf-8") == ""
