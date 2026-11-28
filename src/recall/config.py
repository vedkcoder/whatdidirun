"""Configuration: a TOML file overridden by RECALL_* env vars.

Default locations (overridable with RECALL_CONFIG / RECALL_DATA_DIR):
  Linux/macOS  config ~/.config/recall/config.toml   data ~/.local/share/recall
  Windows      config %APPDATA%\\recall\\config.toml   data %LOCALAPPDATA%\\recall
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def _win_dir(env: str, fallback: str) -> Path:
    return Path(os.environ.get(env) or Path.home() / fallback) / "recall"


def default_config_path(windows: bool = IS_WINDOWS) -> Path:
    return _win_dir("APPDATA", "AppData/Roaming") / "config.toml" if windows else Path("~/.config/recall/config.toml").expanduser()


def default_data_dir(windows: bool = IS_WINDOWS) -> Path:
    return _win_dir("LOCALAPPDATA", "AppData/Local") if windows else Path("~/.local/share/recall").expanduser()


CONFIG_PATH = Path(os.environ["RECALL_CONFIG"]).expanduser() if os.environ.get("RECALL_CONFIG") else default_config_path()

# (config attribute, env var, type)
_ENV = [
    ("data_dir", "RECALL_DATA_DIR", Path),
    ("llm_base_url", "RECALL_LLM_BASE_URL", str),
    ("llm_api_key", "RECALL_LLM_API_KEY", str),
    ("chat_provider", "RECALL_CHAT_PROVIDER", str),
    ("embed_provider", "RECALL_EMBED_PROVIDER", str),
    ("embed_model", "RECALL_EMBED_MODEL", str),
    ("chat_model", "RECALL_CHAT_MODEL", str),
    ("llm_enabled", "RECALL_LLM_ENABLED", bool),
    ("llm_timeout", "RECALL_LLM_TIMEOUT", float),
    ("reasoning_effort", "RECALL_REASONING_EFFORT", str),
    ("describe", "RECALL_DESCRIBE", bool),
    ("web_host", "RECALL_WEB_HOST", str),
    ("web_port", "RECALL_WEB_PORT", int),
]


def _to_bool(v: str | bool) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    data_dir: Path = field(default_factory=default_data_dir)
    # Which provider serves chat (descriptions, smart search, explain) and which serves embeddings.
    # See providers/catalog.py for the list; LM Studio is the default for both.
    chat_provider: str = "lmstudio"
    embed_provider: str = "lmstudio"
    chat_model: str = ""  # empty = auto-pick the first non-embedding model (local providers only)
    embed_model: str = ""  # empty = the provider's default embedding model
    # Per-provider settings from config.toml: [providers.openai] base_url = "...", api_key = "..."
    providers: dict = field(default_factory=dict)
    # Legacy (0.1): LM Studio's address and key. Still honoured for the lmstudio provider.
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_enabled: bool = True
    llm_timeout: float = 60.0
    # Sent as `reasoning_effort` on chat calls. "none" stops thinking models (e.g. Qwen 3.5) from spending
    # their whole budget reasoning, ~30x faster for our short tasks. Set to "" to omit the parameter.
    reasoning_effort: str = "none"
    describe: bool = True  # generate plain-English descriptions of commands at ingest (uses the chat model)
    web_host: str = "127.0.0.1"  # the UI has no auth; only bind wider inside a container
    web_port: int = 8765
    # Commands matching any of these regexes are never stored.
    ignore_patterns: list[str] = field(default_factory=lambda: [r"^\s*$", r"^(ls|cd|pwd|clear|exit|history)\s*$"])
    # Which attributes came from env vars / config.toml (settings saved in the UI sit between the two).
    env_overrides: set[str] = field(default_factory=set, repr=False, compare=False)
    file_values: set[str] = field(default_factory=set, repr=False, compare=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "recall.db"

    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def spool_path(self) -> Path:
        return self.data_dir / "spool.jsonl"


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or CONFIG_PATH
    if path.exists():
        data = tomllib.loads(path.read_text())
        for key, value in data.items():
            if hasattr(cfg, key) and key not in ("env_overrides", "file_values"):
                setattr(cfg, key, value)
                cfg.file_values.add(key)
    for attr, env, typ in _ENV:
        if (raw := os.environ.get(env)) is not None and raw != "":
            setattr(cfg, attr, _to_bool(raw) if typ is bool else typ(raw))
            cfg.env_overrides.add(attr)
    cfg.data_dir = Path(cfg.data_dir).expanduser()
    cfg.llm_enabled = _to_bool(cfg.llm_enabled)
    cfg.describe = _to_bool(cfg.describe)
    return cfg
