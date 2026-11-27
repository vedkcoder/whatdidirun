"""Shell integration: where the capture hooks live and how they get into the user's profile."""

from __future__ import annotations

import os
import re
from pathlib import Path

CAPTURE_DIR = Path(__file__).parent / "capture"
BASH_HOOK = CAPTURE_DIR / "recall.bash"
PWSH_HOOK = CAPTURE_DIR / "recall.ps1"

BEGIN = "# >>> whatdidirun (recall) >>>"
END = "# <<< whatdidirun (recall) <<<"
_BLOCK = re.compile(rf"\r?\n?{re.escape(BEGIN)}.*?{re.escape(END)}\r?\n?", re.S)


def bash_line() -> str:
    return f'source "{BASH_HOOK}"'


def powershell_line() -> str:
    return f'. "{PWSH_HOOK}"'


def documents_dir() -> Path:
    """The user's Documents folder (honours OneDrive / folder redirection on Windows)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5 (Documents), SHGFP_TYPE_CURRENT = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:  # noqa: BLE001 - fall back to the conventional location
            pass
    return Path.home() / "Documents"


def powershell_profiles(docs: Path | None = None) -> list[Path]:
    """CurrentUserCurrentHost profiles for PowerShell 7+ and Windows PowerShell 5.1."""
    docs = docs or documents_dir()
    if os.name != "nt" and docs == Path.home() / "Documents":
        # PowerShell 7 on Linux/macOS
        return [Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "powershell/Microsoft.PowerShell_profile.ps1"]
    return [
        docs / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
        docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
    ]


def _block(line: str) -> str:
    return f"{BEGIN}\n{line}\n{END}\n"


def install_profile(profile: Path, line: str) -> bool:
    """Add (or refresh) the marked hook block. Returns True if the file changed."""
    text = profile.read_text(encoding="utf-8-sig") if profile.exists() else ""
    stripped = _BLOCK.sub("\n", text).rstrip("\r\n")
    new = (stripped + "\n\n" if stripped else "") + _block(line)
    if new == text:
        return False
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(new, encoding="utf-8-sig")  # BOM: Windows PowerShell 5.1 reads BOM-less files as ANSI
    return True


def uninstall_profile(profile: Path) -> bool:
    """Remove the marked hook block. Returns True if the file changed."""
    if not profile.exists():
        return False
    text = profile.read_text(encoding="utf-8-sig")
    if BEGIN not in text:
        return False
    new = _BLOCK.sub("\n", text).strip("\r\n")
    profile.write_text(new + "\n" if new else "", encoding="utf-8-sig")
    return True
