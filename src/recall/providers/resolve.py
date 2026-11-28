"""Turn config (+ saved settings) into concrete provider specs.

Precedence, highest first, for every value:
  1. environment variables (RECALL_*, and the standard key variables such as OPENAI_API_KEY)
  2. settings saved from the web UI / `recall config` (stored in the database)
  3. config.toml
  4. built-in defaults (the catalog)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from ..config import Config
from .base import ProviderError, Spec
from .catalog import ProviderInfo, get


class SettingsSource(Protocol):
    """What the resolver needs from the settings store (the database). None = nothing saved."""

    def get(self, key: str) -> str | None: ...

    def secret(self, provider: str) -> str | None: ...


@dataclass
class Value:
    value: str
    source: str  # "env", "settings", "file" or "default"


def _env_name(provider: str, what: str) -> str:
    return f"RECALL_{provider.upper()}_{what}"


def _first(*candidates: tuple[str, str | None]) -> Value:
    for source, value in candidates:
        if value:
            return Value(str(value), source)
    return Value("", "default")


def _cfg_value(cfg: Config, settings: SettingsSource | None, attr: str, settings_key: str | None = None) -> Value:
    """A top-level config value: env (already applied to cfg) > settings > file/default (cfg)."""
    raw = getattr(cfg, attr)
    if attr in cfg.env_overrides:
        return Value(str(raw), "env")
    saved = settings.get(settings_key or attr) if settings else None
    if saved is not None and saved != "":
        return Value(saved, "settings")
    return Value(str(raw), "file" if attr in cfg.file_values else "default")


def _file_provider(cfg: Config, provider: str, key: str) -> str | None:
    table = cfg.providers.get(provider) if isinstance(cfg.providers, dict) else None
    value = table.get(key) if isinstance(table, dict) else None
    return str(value) if value else None


def _legacy(cfg: Config, provider: str, attr: str) -> tuple[str | None, str | None]:
    """RECALL_LLM_BASE_URL / RECALL_LLM_API_KEY (and llm_* in config.toml) predate providers and mean LM Studio.

    Returns (env value, file value); a non-default value set in code counts as a file value.
    """
    if provider != "lmstudio":
        return None, None
    value = getattr(cfg, attr)
    if attr in cfg.env_overrides:
        return value, None
    if attr in cfg.file_values or value != getattr(Config, attr):
        return None, value
    return None, None


def base_url(cfg: Config, provider: str, settings: SettingsSource | None = None) -> Value:
    info = get(provider)
    legacy_env, legacy_file = _legacy(cfg, provider, "llm_base_url")
    v = _first(
        ("env", os.environ.get(_env_name(provider, "BASE_URL"))),
        ("env", legacy_env),
        ("settings", settings.get(f"provider.{provider}.base_url") if settings else None),
        ("file", _file_provider(cfg, provider, "base_url")),
        ("file", legacy_file),
        ("default", info.base_url),
    )
    url = v.value.rstrip("/")
    if info.normalize_v1 and url and not url.endswith("/v1"):
        url += "/v1"
    return Value(url, v.source)


def api_key(cfg: Config, provider: str, settings: SettingsSource | None = None) -> Value:
    info = get(provider)
    legacy_env, legacy_file = _legacy(cfg, provider, "llm_api_key")
    return _first(
        ("env", os.environ.get(_env_name(provider, "API_KEY"))),
        *(("env", os.environ.get(name)) for name in info.key_env),
        ("env", legacy_env),
        ("settings", settings.secret(provider) if settings else None),
        ("file", _file_provider(cfg, provider, "api_key")),
        ("file", legacy_file),
        ("default", info.default_key),
    )


def chat_provider(cfg: Config, settings: SettingsSource | None = None) -> Value:
    return _cfg_value(cfg, settings, "chat_provider")


def embed_provider(cfg: Config, settings: SettingsSource | None = None) -> Value:
    return _cfg_value(cfg, settings, "embed_provider")


def setting(cfg: Config, settings: SettingsSource | None, attr: str) -> Value:
    return _cfg_value(cfg, settings, attr)


def _spec(cfg: Config, settings: SettingsSource | None, info: ProviderInfo, model: str) -> Spec:
    url = base_url(cfg, info.id, settings).value
    if not url:
        raise ProviderError(f"{info.label}: no base URL configured (set it in Settings or RECALL_{info.id.upper()}_BASE_URL)")
    reasoning = setting(cfg, settings, "reasoning_effort").value
    timeout = float(setting(cfg, settings, "llm_timeout").value or 60)
    return Spec(
        provider=info.id,
        label=info.label,
        base_url=url,
        api_key=api_key(cfg, info.id, settings).value,
        model=model,
        timeout=timeout,
        reasoning_effort="" if reasoning in ("", "omit") else reasoning,
        headers=dict(info.headers),
        json_mode=info.json_mode,
    )


def chat_spec(cfg: Config, settings: SettingsSource | None = None) -> Spec:
    """The chat provider spec. `model` may be "" for local providers: the client auto-picks one."""
    info = get(chat_provider(cfg, settings).value)
    if not info.chat:
        raise ProviderError(f"{info.label} doesn't offer chat models")
    return _spec(cfg, settings, info, setting(cfg, settings, "chat_model").value)


def embed_spec(cfg: Config, settings: SettingsSource | None = None) -> Spec:
    info = get(embed_provider(cfg, settings).value)
    if not info.embeddings:
        raise ProviderError(f"{info.label} has no embeddings API; choose another embedding provider")
    model = setting(cfg, settings, "embed_model").value or info.default_embed_model
    if not model:
        raise ProviderError(f"Choose an embedding model for {info.label} (Settings, or RECALL_EMBED_MODEL)")
    return _spec(cfg, settings, info, model)


def embed_id(spec: Spec) -> str:
    """Identifies the vector space. LM Studio keeps the bare model name so 0.1 indexes are reused as-is."""
    return spec.model if spec.provider == "lmstudio" else f"{spec.provider}:{spec.model}"
