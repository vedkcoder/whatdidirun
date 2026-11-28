"""Settings saved from the web UI or `recall config` / `recall keys`, stored in the SQLite database.

Precedence (see providers/resolve.py): environment variables > these saved settings > config.toml > defaults.
API keys are encrypted (keystore.py) and only ever leave this module in full through `secret()`, which the
provider resolver and the Settings page's Reveal button use.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from urllib.parse import urlparse

from .config import Config
from .db import connect, get_meta, set_meta
from .keystore import KeyStore, KeyStoreError
from .providers import catalog

CUSTOM_KEY = "custom_providers"
_BOOL = {"llm_enabled", "describe"}
AUTO_KEYS = {"chat_model", "embed_model"}
KNOWN = {"chat_provider", "embed_provider", "chat_model", "embed_model", "reasoning_effort", "llm_timeout", *_BOOL}


class SettingsError(ValueError):
    """A value that can't be saved (unknown key, bad URL, unknown provider, ...)."""


def _check_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SettingsError(f"not an http(s) URL: {url!r}")
    return url


def validate(key: str, value: str) -> str:
    """Normalise a setting or raise SettingsError. Only these keys can be saved."""
    value = str(value).strip()
    if key == "chat_provider":
        info = _provider(value)
        if not info.chat:
            raise SettingsError(f"{info.label} doesn't offer chat models")
    elif key == "embed_provider":
        info = _provider(value)
        if not info.embeddings:
            raise SettingsError(f"{info.label} has no embeddings API")
    elif key in ("chat_model", "embed_model", "reasoning_effort"):
        if len(value) > 200:
            raise SettingsError(f"{key} is too long")
    elif key in _BOOL:
        if value.lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            raise SettingsError(f"{key} must be on or off")
        value = "1" if value.lower() in ("1", "true", "yes", "on") else "0"
    elif key == "llm_timeout":
        try:
            if not 1 <= float(value) <= 600:
                raise ValueError
        except ValueError:
            raise SettingsError("llm_timeout must be between 1 and 600 seconds") from None
    elif m := re.fullmatch(r"provider\.([A-Za-z0-9_-]+)\.base_url", key):
        _provider(m.group(1))
        value = _check_url(value)
    else:
        raise SettingsError(f"unknown setting {key!r}")
    return value


def is_known(key: str) -> bool:
    return key in KNOWN or re.fullmatch(r"provider\.[A-Za-z0-9_-]+\.base_url", key) is not None


def _provider(provider_id: str) -> catalog.ProviderInfo:
    try:
        return catalog.get(provider_id)
    except ValueError as e:
        raise SettingsError(str(e)) from None


class SettingsStore:
    """Thread-safe; uses its own SQLite connection. Implements providers.resolve.SettingsSource."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.conn: sqlite3.Connection = connect(cfg.db_path)
        self.keys = KeyStore(cfg.data_dir)
        self.lock = threading.RLock()
        self.decrypt_errors: dict[str, str] = {}
        self._load_custom()

    # ---- change tracking (lets long-running processes notice changes made by others) ----------------
    def revision(self) -> str:
        with self.lock:
            return get_meta(self.conn, "settings_revision") or "0"

    def _bump(self) -> None:
        set_meta(self.conn, "settings_revision", str(time.time_ns()))
        self.conn.commit()
        self._load_custom()

    # ---- plain settings ---------------------------------------------------------------------------
    def get(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def all(self) -> dict[str, str]:
        with self.lock:
            return {k: v for k, v in self.conn.execute("SELECT key, value FROM settings WHERE key != ?", (CUSTOM_KEY,))}

    def update(self, values: dict[str, str | None]) -> None:
        """Save several settings at once. None removes a setting (falling back to config.toml / default)."""
        # For the model settings "" is a real value: "auto / the provider's default" (it must beat a model
        # name from config.toml that belonged to another provider). Elsewhere "" means remove.
        clean: dict[str, str | None] = {}
        for k, v in values.items():
            if not is_known(k):
                raise SettingsError(f"unknown setting {k!r}")
            if v is None:
                clean[k] = None  # remove
            elif v == "":
                clean[k] = "" if k in AUTO_KEYS else None
            else:
                clean[k] = validate(k, v)
        with self.lock:
            for k, v in clean.items():
                if v is None:
                    self.conn.execute("DELETE FROM settings WHERE key = ?", (k,))
                else:
                    self.conn.execute(
                        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v)
                    )
            self._bump()

    def set(self, key: str, value: str | None) -> None:
        self.update({key: value})

    # ---- API keys ---------------------------------------------------------------------------------
    def secret(self, provider: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT nonce, ciphertext FROM secrets WHERE provider = ?", (provider,)).fetchone()
        if not row:
            return None
        try:
            value = self.keys.decrypt(row[0], row[1], provider)
            self.decrypt_errors.pop(provider, None)
            return value
        except KeyStoreError as e:
            self.decrypt_errors[provider] = str(e)
            return None

    def hint(self, provider: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT hint FROM secrets WHERE provider = ?", (provider,)).fetchone()
        return row[0] if row else None

    def set_secret(self, provider: str, key: str) -> None:
        _provider(provider)
        key = key.strip()
        if not key or len(key) > 4096 or any(c.isspace() for c in key):
            raise SettingsError("that doesn't look like an API key")
        nonce, ct = self.keys.encrypt(key, provider)
        hint = "…" + key[-4:] if len(key) > 8 else "…"
        with self.lock:
            self.conn.execute(
                "INSERT INTO secrets(provider, nonce, ciphertext, hint, updated) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET nonce = excluded.nonce, ciphertext = excluded.ciphertext, "
                "hint = excluded.hint, updated = excluded.updated",
                (provider, nonce, ct, hint, time.time()),
            )
            self._bump()
        self.decrypt_errors.pop(provider, None)

    def delete_secret(self, provider: str) -> bool:
        with self.lock:
            n = self.conn.execute("DELETE FROM secrets WHERE provider = ?", (provider,)).rowcount
            self._bump()
        return bool(n)

    # ---- custom OpenAI-compatible endpoints -------------------------------------------------------
    def custom(self) -> list[dict]:
        raw = self.get(CUSTOM_KEY)
        try:
            entries = json.loads(raw) if raw else []
        except ValueError:
            entries = []
        return [e for e in entries if isinstance(e, dict) and e.get("id")]

    def _load_custom(self) -> None:
        catalog.set_custom(self.custom())

    def _save_custom(self, entries: list[dict]) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (CUSTOM_KEY, json.dumps(entries)),
            )
            self._bump()

    def add_custom(self, label: str, base_url: str, embeddings: bool = True, api_key: str = "") -> str:
        label = label.strip()[:60]
        if not label:
            raise SettingsError("give the endpoint a name")
        url = _check_url(base_url)
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30] or "endpoint"
        taken = {p.id for p in catalog.all_providers()}
        pid, n = f"custom-{slug}", 2
        while pid in taken:
            pid, n = f"custom-{slug}-{n}", n + 1
        with self.lock:
            self._save_custom([*self.custom(), {"id": pid, "label": label, "base_url": url, "embeddings": bool(embeddings)}])
            if api_key:
                self.set_secret(pid, api_key)
        return pid

    def update_custom(self, pid: str, label: str | None = None, base_url: str | None = None, embeddings: bool | None = None) -> None:
        with self.lock:
            entries = self.custom()
            for e in entries:
                if e["id"] == pid:
                    if label is not None:
                        if not label.strip():
                            raise SettingsError("give the endpoint a name")
                        e["label"] = label.strip()[:60]
                    if base_url is not None:
                        e["base_url"] = _check_url(base_url)
                    if embeddings is not None:
                        e["embeddings"] = bool(embeddings)
                    self._save_custom(entries)
                    return
        raise SettingsError(f"unknown custom endpoint {pid!r}")

    def remove_custom(self, pid: str) -> None:
        with self.lock:
            entries = self.custom()
            if not any(e["id"] == pid for e in entries):
                raise SettingsError(f"unknown custom endpoint {pid!r}")
            self._save_custom([e for e in entries if e["id"] != pid])
            self.conn.execute("DELETE FROM secrets WHERE provider = ?", (pid,))
            # Don't leave chat/embeddings pointing at an endpoint that no longer exists.
            for key in ("chat_provider", "embed_provider"):
                if self.get(key) == pid:
                    self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            self._bump()


_stores: dict[str, SettingsStore] = {}
_stores_lock = threading.Lock()


def store_for(cfg: Config) -> SettingsStore:
    """One SettingsStore per database per process."""
    path = str(cfg.db_path)
    with _stores_lock:
        if path not in _stores:
            _stores[path] = SettingsStore(cfg)
        return _stores[path]


class Overlay:
    """Saved settings with unsaved values from the Settings form on top (for the Test button)."""

    def __init__(self, base: SettingsStore | None, values: dict[str, str] | None = None, secrets: dict[str, str] | None = None):
        self.base, self.values, self.secrets = base, values or {}, secrets or {}

    def get(self, key: str) -> str | None:
        if key in self.values:
            return self.values[key]
        return self.base.get(key) if self.base else None

    def secret(self, provider: str) -> str | None:
        if provider in self.secrets:
            return self.secrets[provider]
        return self.base.secret(provider) if self.base else None
