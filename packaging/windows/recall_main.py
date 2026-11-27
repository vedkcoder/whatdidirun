"""PyInstaller entry point for recall.exe (console CLI)."""

from recall.cli import app

if __name__ == "__main__":
    app(prog_name="recall")
