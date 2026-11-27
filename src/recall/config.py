"""Configuration: ~/.config/recall/config.toml, overridden by RECALL_* env vars."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("RECALL_CONFIG", "~/.config/recall/config.toml")).expanduser()

# (config attribute, env var, type)
_ENV = [
    ("data_dir", "RECALL_DATA_DIR", Path),
    ("llm_base_url", "RECALL_LLM_BASE_URL", str),
    ("llm_api_key", "RECALL_LLM_API_KEY", str),
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
    data_dir: Path = Path("~/.local/share/recall").expanduser()
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    embed_model: str = "text-embedding-nomic-embed-text-v1.5"
    chat_model: str = ""  # empty = auto-pick the first non-embedding model LM Studio reports
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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "recall.db"

    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def spool_path(self) -> Path:
        return self.data_dir / "spool.jsonl"

    @property
    def base_url(self) -> str:
        url = self.llm_base_url.rstrip("/")
        return url if url.endswith("/v1") else url + "/v1"


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or CONFIG_PATH
    if path.exists():
        data = tomllib.loads(path.read_text())
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    for attr, env, typ in _ENV:
        if (raw := os.environ.get(env)) is not None and raw != "":
            setattr(cfg, attr, _to_bool(raw) if typ is bool else typ(raw))
    cfg.data_dir = Path(cfg.data_dir).expanduser()
    cfg.llm_enabled = _to_bool(cfg.llm_enabled)
    cfg.describe = _to_bool(cfg.describe)
    return cfg
