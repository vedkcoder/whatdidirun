"""Anthropic Messages API (Claude). Chat only: Anthropic has no embeddings endpoint."""

from __future__ import annotations

from .base import BadRequestError, ProviderError, Spec, drop_rejected_params, request_json
from .catalog import ProviderInfo

API_VERSION = "2023-06-01"


class Anthropic:
    def __init__(self, spec: Spec, info: ProviderInfo):
        self.spec = spec
        self.info = info

    def _headers(self) -> dict[str, str]:
        return {**self.spec.headers, "x-api-key": self.spec.api_key, "anthropic-version": API_VERSION}

    def models(self, timeout: float = 5.0) -> list[str]:
        data = request_json(self.spec, "GET", f"{self.spec.base_url}/v1/models?limit=1000", self._headers(), timeout=timeout)
        try:
            return sorted(m["id"] for m in data["data"])
        except (KeyError, TypeError) as e:
            raise ProviderError(f"{self.spec.label}: unexpected /v1/models response: {str(data)[:200]}") from e

    def chat(self, system: str, user: str, max_tokens: int = 800, json_mode: bool = False) -> str:
        body: dict = {
            "model": self.spec.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.1,
        }
        url = f"{self.spec.base_url}/v1/messages"
        try:
            data = request_json(self.spec, "POST", url, self._headers(), body)
        except BadRequestError as e:
            if not drop_rejected_params(e, body, ("temperature",)):
                raise
            data = request_json(self.spec, "POST", url, self._headers(), body)
        try:
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()
        except (KeyError, TypeError) as e:
            raise ProviderError(f"{self.spec.label}: unexpected messages response: {str(data)[:200]}") from e
        if not text and data.get("stop_reason") == "max_tokens":
            raise ProviderError(f"{self.spec.label} hit the token limit before answering")
        return text

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        raise ProviderError("Anthropic has no embeddings API; choose another embedding provider")
