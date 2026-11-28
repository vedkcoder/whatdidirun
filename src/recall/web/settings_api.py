"""Settings endpoints: providers, models, API keys, custom endpoints.

These change where your command history is sent and can reveal API keys, so every request must:
  - carry `X-Recall-Request: 1`. Browsers can't add custom headers cross-site without a CORS preflight,
    which this server never approves, so other websites can't call these endpoints (CSRF).
  - name a loopback Host (127.0.0.1 / localhost / [::1]), which defeats DNS rebinding.
  - not come from a foreign Origin.
When the server listens on a network address, writes and Reveal additionally need RECALL_SETTINGS_TOKEN
(sent as `X-Recall-Token`); without it, settings are read-only there.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import Config, _to_bool
from ..llm import LLMClient
from ..providers import ProviderError, make_adapter
from ..providers import resolve as R
from ..providers.catalog import CUSTOM, all_providers, get
from ..settings import Overlay, SettingsError, store_for

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SHOWN = ["chat_provider", "chat_model", "embed_provider", "embed_model", "llm_enabled", "describe", "reasoning_effort", "llm_timeout"]


def _host_only(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class Access:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def token(self) -> str:
        return os.environ.get("RECALL_SETTINGS_TOKEN", "")

    @property
    def network_bound(self) -> bool:
        return _host_only(self.cfg.web_host or "127.0.0.1") not in LOOPBACK

    @property
    def writable(self) -> bool:
        return bool(self.token) or not self.network_bound

    def check(self, request: Request, write: bool) -> None:
        if request.headers.get("x-recall-request") != "1":
            raise HTTPException(403, "missing X-Recall-Request header")
        host = _host_only(request.headers.get("host", ""))
        origin = request.headers.get("origin")
        if origin and origin != "null":
            o = urlparse(origin)
            if _host_only(o.netloc) != host:
                raise HTTPException(403, "cross-origin request refused")
        if self.token:
            if not hmac.compare_digest(request.headers.get("x-recall-token", ""), self.token):
                raise HTTPException(401, "settings token required (RECALL_SETTINGS_TOKEN)")
            return
        if self.network_bound:
            if write:
                raise HTTPException(403, "settings are read-only while the server listens on the network; set RECALL_SETTINGS_TOKEN")
            return
        if host not in LOOPBACK:
            raise HTTPException(403, f"unexpected Host {host!r}")


class ValuesIn(BaseModel):
    values: dict[str, str | None]


class KeyIn(BaseModel):
    api_key: str


class TestIn(BaseModel):
    kind: str  # "chat" or "embed"
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""


class ModelsIn(BaseModel):
    base_url: str = ""
    api_key: str = ""


class CustomIn(BaseModel):
    label: str
    base_url: str
    embeddings: bool = True
    api_key: str = ""


class CustomUpdate(BaseModel):
    label: str | None = None
    base_url: str | None = None
    embeddings: bool | None = None


def router(cfg: Config, on_change) -> APIRouter:
    """`on_change()` is called after anything is saved (the app rebuilds its provider client)."""
    access = Access(cfg)
    store = store_for(cfg)
    api = APIRouter(prefix="/api")
    reveal_lock = threading.Lock()

    def read(request: Request):
        access.check(request, write=False)

    def write(request: Request):
        access.check(request, write=True)

    def bad(e: Exception):
        return HTTPException(400, str(e))

    def overlay(provider: str, base_url: str, api_key: str) -> Overlay:
        values = {f"provider.{provider}.base_url": base_url} if base_url else {}
        return Overlay(store, values, {provider: api_key} if api_key else {})

    def provider_view(p) -> dict:
        key = R.api_key(cfg, p.id, store)
        url = R.base_url(cfg, p.id, store)
        saved_hint = store.hint(p.id)
        return {
            "id": p.id,
            "label": p.label,
            "chat": p.chat,
            "embeddings": p.embeddings,
            "needs_key": p.needs_key,
            "local": p.local,
            "custom": p.id in CUSTOM or p.id == "custom",
            "removable": p.id in CUSTOM,
            "key_url": p.key_url,
            "key_env": list(p.key_env),
            "default_base_url": p.base_url,
            "default_embed_model": p.default_embed_model,
            "base_url": {"value": url.value, "source": url.source},
            "key": {
                "set": bool(key.value) and key.value != p.default_key,
                "source": key.source if key.value and key.value != p.default_key else None,
                "saved": saved_hint is not None,
                "hint": saved_hint,
                "error": store.decrypt_errors.get(p.id),
            },
        }

    @api.get("/settings", dependencies=[Depends(read)])
    def get_settings():
        values = {}
        for k in SHOWN:
            v = R.setting(cfg, store, k)
            value: str | bool = _to_bool(v.value) if k in ("llm_enabled", "describe") else v.value
            values[k] = {"value": value, "source": v.source}
        with store.lock:
            commands = store.conn.execute("SELECT count(*) FROM cmd_texts").fetchone()[0]
        lm = LLMClient(cfg, store)
        try:
            embed_id = lm.embed_id
        except ProviderError:
            embed_id = ""
        return {
            "writable": access.writable,
            "reveal": access.writable,
            "token_required": bool(access.token),
            "key_backend": store.keys.backend,
            "values": values,
            "providers": [provider_view(p) for p in all_providers()],
            "commands": commands,
            "embed_id": embed_id,
        }

    @api.put("/settings", dependencies=[Depends(write)])
    def put_settings(body: ValuesIn):
        try:
            store.update(body.values)
        except SettingsError as e:
            raise bad(e) from e
        on_change()
        return get_settings()

    @api.put("/settings/keys/{provider}", dependencies=[Depends(write)])
    def put_key(provider: str, body: KeyIn):
        try:
            store.set_secret(provider, body.api_key)
        except SettingsError as e:
            raise bad(e) from e
        on_change()
        return {"ok": True, "hint": store.hint(provider)}

    @api.delete("/settings/keys/{provider}", dependencies=[Depends(write)])
    def delete_key(provider: str):
        removed = store.delete_secret(provider)
        on_change()
        return {"ok": removed}

    @api.post("/settings/keys/{provider}/reveal", dependencies=[Depends(write)])
    def reveal_key(provider: str):
        with reveal_lock:
            time.sleep(0.2)  # slow down anything scripted
            key = store.secret(provider)
        if key is None:
            raise HTTPException(404, store.decrypt_errors.get(provider) or "no saved key")
        return {"api_key": key}

    @api.post("/providers/{provider}/models", dependencies=[Depends(read)])
    def list_models(provider: str, body: ModelsIn):
        try:
            info = get(provider)
            spec = R.spec_for(cfg, overlay(provider, body.base_url, body.api_key), provider, "x", "chat" if info.chat else "embed")
            LLMClient._require_key(spec)
            return {"models": make_adapter(spec).models(timeout=8)}
        except (ProviderError, ValueError) as e:
            raise HTTPException(502, str(e)) from e

    @api.post("/settings/test", dependencies=[Depends(read)])
    def test(body: TestIn):
        if body.kind not in ("chat", "embed"):
            raise HTTPException(400, "kind must be chat or embed")
        t0 = time.monotonic()
        try:
            src = overlay(body.provider, body.base_url, body.api_key)
            spec = R.spec_for(cfg, src, body.provider, body.model, body.kind)
            LLMClient._require_key(spec)
            adapter = make_adapter(spec)
            if body.kind == "embed":
                dims = len(adapter.embed(["test"], kind="query")[0])
                return {"ok": True, "model": spec.model, "dims": dims, "ms": round((time.monotonic() - t0) * 1000)}
            if not spec.model:
                info = get(body.provider)
                if not (info.local or body.provider == "custom" or body.provider in CUSTOM):
                    raise ProviderError(f"Choose a chat model for {spec.label}")
                spec.model = next((m for m in adapter.models() if "embed" not in m.lower()), "")
                if not spec.model:
                    raise ProviderError(f"{spec.label} has no chat model available")
            reply = adapter.chat("Reply with exactly: ok", "ping", max_tokens=50)
            return {"ok": True, "model": spec.model, "reply": reply[:80], "ms": round((time.monotonic() - t0) * 1000)}
        except (ProviderError, ValueError) as e:
            return {"ok": False, "error": str(e), "ms": round((time.monotonic() - t0) * 1000)}

    @api.post("/settings/custom", dependencies=[Depends(write)])
    def add_custom(body: CustomIn):
        try:
            pid = store.add_custom(body.label, body.base_url, body.embeddings, body.api_key)
        except SettingsError as e:
            raise bad(e) from e
        on_change()
        return {"id": pid}

    @api.put("/settings/custom/{provider}", dependencies=[Depends(write)])
    def update_custom(provider: str, body: CustomUpdate):
        try:
            store.update_custom(provider, body.label, body.base_url, body.embeddings)
        except SettingsError as e:
            raise bad(e) from e
        on_change()
        return {"ok": True}

    @api.delete("/settings/custom/{provider}", dependencies=[Depends(write)])
    def delete_custom(provider: str):
        try:
            store.remove_custom(provider)
        except SettingsError as e:
            raise bad(e) from e
        on_change()
        return {"ok": True}

    return api
