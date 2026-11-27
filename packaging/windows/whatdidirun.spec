# PyInstaller spec: one folder with two executables sharing one runtime.
#   recall.exe       console CLI (PATH, PowerShell hook, Ctrl-G picker)
#   WhatDidIRun.exe  windowed tray app (web UI + background indexer)
# Build from the repo root on Windows:  uv run pyinstaller packaging/windows/whatdidirun.spec --noconfirm
# The web UI must be built first (cd web && npm ci && npm run build).

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent.parent
PKG = ROOT / "src" / "recall"
ICON = str(PKG / "assets" / "icon.ico")

if not (PKG / "web" / "dist" / "index.html").exists():
    raise SystemExit("web UI not built: run `npm ci && npm run build` in web/ first")

datas = [
    (str(PKG / "web" / "dist"), "recall/web/dist"),
    (str(PKG / "capture"), "recall/capture"),
    (str(PKG / "assets"), "recall/assets"),
]
binaries = []
hiddenimports = [
    "recall.tray",
    "pystray._win32",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]
for pkg in ("chromadb", "chromadb_rust_bindings", "textual", "dateparser", "tzdata", "onnxruntime", "tokenizers"):
    if importlib.util.find_spec(pkg) is None:
        continue
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h


def analysis(script):
    return Analysis(
        [str(Path(SPECPATH) / script)],
        pathex=[str(ROOT / "src")],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=["tkinter", "matplotlib", "IPython", "pytest"],
        noarchive=False,
    )


cli_a = analysis("recall_main.py")
tray_a = analysis("tray_main.py")

cli_exe = EXE(
    PYZ(cli_a.pure),
    cli_a.scripts,
    exclude_binaries=True,
    name="recall",
    console=True,
    icon=ICON,
)
tray_exe = EXE(
    PYZ(tray_a.pure),
    tray_a.scripts,
    exclude_binaries=True,
    name="WhatDidIRun",
    console=False,
    icon=ICON,
)

coll = COLLECT(
    cli_exe,
    cli_a.binaries,
    cli_a.datas,
    tray_exe,
    tray_a.binaries,
    tray_a.datas,
    name="WhatDidIRun",
)
