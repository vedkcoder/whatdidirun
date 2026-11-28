"""The one object the rest of recall uses for AI features: a chat provider plus an embedding provider.

They are resolved separately because several chat providers (Anthropic, Groq, DeepSeek, ...) have no
embeddings API. `available()` means "embeddings reachable", which is what search depends on.
"""

from __future__ import annotations

import json
import re
from functools import cached_property

from .config import Config, _to_bool
from .providers import ProviderError, Spec, make_adapter
from .providers import resolve as R
from .providers.base import AuthError, UnavailableError
from .providers.catalog import get

LLMError = ProviderError


class LLMClient:
    def __init__(self, cfg: Config, settings: R.SettingsSource | None = None):
        self.cfg = cfg
        self.settings = settings

    # ---- resolution (lazy: a bad provider setting only fails the features that use it) ---------------
    def _resolve(self, fn) -> Spec:
        try:
            return fn(self.cfg, self.settings)
        except ValueError as e:  # unknown provider id
            raise ProviderError(str(e)) from None

    @cached_property
    def chat_spec(self) -> Spec:
        return self._resolve(R.chat_spec)

    @cached_property
    def embed_spec(self) -> Spec:
        return self._resolve(R.embed_spec)

    @staticmethod
    def _require_key(spec: Spec) -> None:
        info = get(spec.provider)
        if info.needs_key and not spec.api_key:
            env = f"{info.key_env[0]} or " if info.key_env else ""
            raise AuthError(f"No API key for {spec.label}: add one in Settings (or set {env}RECALL_{spec.provider.upper()}_API_KEY)")

    @cached_property
    def _chat(self):
        self._require_key(self.chat_spec)
        return make_adapter(self.chat_spec)

    @cached_property
    def _embedder(self):
        self._require_key(self.embed_spec)
        return make_adapter(self.embed_spec)

    @property
    def llm_enabled(self) -> bool:
        return _to_bool(R.setting(self.cfg, self.settings, "llm_enabled").value)

    @property
    def embed_id(self) -> str:
        return R.embed_id(self.embed_spec)

    @property
    def embed_model(self) -> str:
        return self.embed_spec.model

    def describe_embedder(self) -> str:
        """e.g. 'LM Studio at http://localhost:1234/v1' for messages."""
        try:
            s = self.embed_spec
        except ProviderError:
            return "the embedding provider"
        return f"{s.label} at {s.base_url}"

    def describe_chat(self) -> str:
        try:
            s = self.chat_spec
        except ProviderError:
            return "the chat provider"
        return f"{s.label} at {s.base_url}"

    # ---- health -------------------------------------------------------------------------------------
    def embed_models(self, timeout: float = 5.0) -> list[str]:
        return self._embedder.models(timeout=timeout)

    def chat_models(self, timeout: float = 5.0) -> list[str]:
        return self._chat.models(timeout=timeout)

    models = embed_models  # 0.1 name

    def _reachable(self, adapter_fn, probe) -> bool:
        try:
            adapter_fn(timeout=2.0)
            return True
        except AuthError:
            return False
        except UnavailableError:
            return False
        except ProviderError:
            # Some OpenAI-compatible servers have no /models endpoint: try the real thing once.
            try:
                probe()
                return True
            except ProviderError:
                return False

    def available(self) -> bool:
        """Embeddings reachable (what semantic search needs)."""
        try:
            return self._reachable(self.embed_models, lambda: self._embedder.embed(["ping"], kind="query"))
        except ProviderError:  # misconfigured provider
            return False

    def chat_available(self) -> bool:
        try:
            return self._reachable(self.chat_models, lambda: self.chat("Reply with: ok", "ping", 20))
        except ProviderError:
            return False

    @cached_property
    def chat_model(self) -> str:
        spec = self.chat_spec
        if spec.model:
            return spec.model
        info = get(spec.provider)
        if info.local or spec.provider == "custom":
            for m in self.chat_models():
                if "embed" not in m.lower():
                    return m
            raise ProviderError(f"No chat model available from {spec.label}; load one or set RECALL_CHAT_MODEL")
        raise ProviderError(f"Choose a chat model for {spec.label} (Settings, or RECALL_CHAT_MODEL)")

    # ---- embeddings ---------------------------------------------------------------------------------
    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        return self._embedder.embed(texts, kind=kind)

    # ---- chat ---------------------------------------------------------------------------------------
    def chat(self, system: str, user: str, max_tokens: int = 800, json_mode: bool = False) -> str:
        if not self.llm_enabled:
            raise ProviderError("AI features are turned off (Settings, or RECALL_LLM_ENABLED=0)")
        self.chat_spec.model = self.chat_model
        return self._chat.chat(system, user, max_tokens=max_tokens, json_mode=json_mode)

    def chat_json(self, system: str, user: str, max_tokens: int = 800):
        text = self.chat(system, user, max_tokens, json_mode=True)
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise ProviderError(f"Model did not return JSON: {text[:200]!r}")
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            raise ProviderError(f"Model returned invalid JSON: {e}") from e
