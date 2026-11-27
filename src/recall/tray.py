"""System-tray app: serves the web UI + background indexer, with a small menu. Used by the Windows installer.

Needs the optional `tray` extra (pystray, pillow).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from .config import Config

APP_NAME = "What did I Run ??"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "WhatDidIRun"
ICON = Path(__file__).parent / "assets" / "icon.png"


def ui_url(cfg: Config) -> str:
    host = "127.0.0.1" if cfg.web_host in ("0.0.0.0", "::", "") else cfg.web_host
    return f"http://{host}:{cfg.web_port}"


def already_running(cfg: Config, timeout: float = 1.0) -> bool:
    """True if a recall web server already answers on the configured port."""
    try:
        with urllib.request.urlopen(f"{ui_url(cfg)}/api/health", timeout=timeout) as r:
            return "embed_model" in json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return False


# --- start at login (Windows: HKCU Run key) -------------------------------------------------------------

def _launch_command() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller build: the tray exe itself
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m recall.cli tray'


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
            except FileNotFoundError:
                pass


# --- app -----------------------------------------------------------------------------------------------

def _start_server(cfg: Config):
    import uvicorn

    from .web.api import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(cfg), host=cfg.web_host, port=cfg.web_port, log_level="warning", log_config=None))
    server.install_signal_handlers = lambda: None  # not on the main thread
    threading.Thread(target=server.run, daemon=True, name="recall-web").start()
    return server


def _index_now(cfg: Config, icon) -> None:
    def work():
        from .db import connect
        from .ingest import ingest

        try:
            res = ingest(cfg, connect(cfg.db_path))
            msg = f"Indexed {res.stored} new commands ({res.embedded} embedded)"
            if res.warnings:
                msg += "\n" + res.warnings[0]
        except Exception as e:  # noqa: BLE001
            msg = f"Indexing failed: {e}"
        try:
            icon.notify(msg, APP_NAME)
        except Exception:  # noqa: BLE001 - notifications aren't supported everywhere
            pass

    threading.Thread(target=work, daemon=True).start()


def run(cfg: Config) -> None:
    import pystray
    from PIL import Image

    if already_running(cfg):
        webbrowser.open(ui_url(cfg))
        return

    server = _start_server(cfg)
    open_ui = lambda icon=None, item=None: webbrowser.open(ui_url(cfg))  # noqa: E731

    def toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    def quit_app(icon, item):
        server.should_exit = True
        icon.stop()

    items = [
        pystray.MenuItem(f"Open {APP_NAME}", open_ui, default=True),
        pystray.MenuItem("Index now", lambda icon, item: _index_now(cfg, icon)),
    ]
    if os.name == "nt":
        items.append(pystray.MenuItem("Start at login", toggle_autostart, checked=lambda item: autostart_enabled()))
    items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Quit", quit_app)]

    icon = pystray.Icon("whatdidirun", Image.open(ICON), APP_NAME, pystray.Menu(*items))
    icon.run()
