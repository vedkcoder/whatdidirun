"""OpenAI-compatible APIs: OpenAI, OpenRouter, Groq, Mistral, DeepSeek, xAI, Together, Ollama, LM Studio, custom."""

from __future__ import annotations

from .base import THINK, BadRequestError, ProviderError, Spec, drop_rejected_params, request_json
from .catalog import ProviderInfo

# Parameters some models reject; dropped and retried once when a 400 names them.
_OPTIONAL = ("temperature", "reasoning_effort", "response_format")


class OpenAICompat:
    def __init__(self, spec: Spec, info: ProviderInfo):
        self.spec = spec
        self.info = info

    def _headers(self) -> dict[str, str]:
        h = dict(self.spec.headers)
        if self.spec.api_key:
            h["Authorization"] = f"Bearer {self.spec.api_key}"
        return h

    def _post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        return request_json(self.spec, "POST", self.spec.base_url + path, self._headers(), body, timeout)

    def models(self, timeout: float = 5.0) -> list[str]:
        data = request_json(self.spec, "GET", self.spec.base_url + "/models", self._headers(), timeout=timeout)
        try:
            return sorted(m["id"] for m in data["data"])
        except (KeyError, TypeError) as e:
            raise ProviderError(f"{self.spec.label}: unexpected /models response: {str(data)[:200]}") from e

    # ---- chat -------------------------------------------------------------------
    def chat(self, system: str, user: str, max_tokens: int = 800, json_mode: bool = False) -> str:
        body: dict = {
            "model": self.spec.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            self.info.max_tokens_field: max_tokens,
        }
        # "none" stops thinking models from burning the whole budget on reasoning (see config.py).
        if self.spec.reasoning_effort and self.info.send_reasoning_effort:
            body["reasoning_effort"] = self.spec.reasoning_effort
        if json_mode and self.spec.json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data = self._post("/chat/completions", body)
        except BadRequestError as e:
            if not drop_rejected_params(e, body, _OPTIONAL):
                raise
            data = self._post("/chat/completions", body)
        try:
            choice = data["choices"][0]
            text = THINK.sub("", choice["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.spec.label}: unexpected chat response: {str(data)[:200]}") from e
        if not text and choice.get("finish_reason") == "length":
            raise ProviderError(
                f"{self.spec.label} model used its whole token budget thinking; "
                "try reasoning effort 'none' or another model"
            )
        return text

    # ---- embeddings -------------------------------------------------------------
    def _prefix(self, kind: str) -> str:
        # nomic-embed models are trained with task prefixes and work noticeably better with them.
        if "nomic" in self.spec.model.lower():
            return "search_query: " if kind == "query" else "search_document: "
        return ""

    def embed(self, texts: list[str], kind: str = "document", batch_size: int = 64) -> list[list[float]]:
        prefix = self._prefix(kind)
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = [prefix + t for t in texts[i : i + batch_size]]
            data = self._post("/embeddings", {"model": self.spec.model, "input": batch})
            try:
                out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
            except (KeyError, TypeError) as e:
                raise ProviderError(f"{self.spec.label}: unexpected embeddings response: {str(data)[:200]}") from e
        return out
