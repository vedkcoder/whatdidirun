"""Google Gemini API (generativelanguage.googleapis.com): chat and embeddings."""

from __future__ import annotations

from urllib.parse import quote

from .base import BadRequestError, ProviderError, Spec, request_json
from .catalog import ProviderInfo


def _model_path(model: str) -> str:
    return model if model.startswith(("models/", "tunedModels/")) else f"models/{model}"


class Gemini:
    def __init__(self, spec: Spec, info: ProviderInfo):
        self.spec = spec
        self.info = info

    def _headers(self) -> dict[str, str]:
        return {**self.spec.headers, "x-goog-api-key": self.spec.api_key}

    def _post(self, path: str, body: dict) -> dict:
        return request_json(self.spec, "POST", f"{self.spec.base_url}/{path}", self._headers(), body)

    def models(self, timeout: float = 5.0) -> list[str]:
        out: list[str] = []
        token = ""
        for _ in range(20):  # paginated
            url = f"{self.spec.base_url}/models?pageSize=1000" + (f"&pageToken={quote(token)}" if token else "")
            data = request_json(self.spec, "GET", url, self._headers(), timeout=timeout)
            try:
                out += [m["name"].removeprefix("models/") for m in data.get("models", [])]
            except (KeyError, TypeError) as e:
                raise ProviderError(f"{self.spec.label}: unexpected /models response: {str(data)[:200]}") from e
            token = data.get("nextPageToken", "")
            if not token:
                break
        return sorted(out)

    def chat(self, system: str, user: str, max_tokens: int = 800, json_mode: bool = False) -> str:
        gen: dict = {"temperature": 0.1, "maxOutputTokens": max_tokens}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        path = f"{_model_path(self.spec.model)}:generateContent"
        try:
            data = self._post(path, body)
        except BadRequestError as e:
            text = (e.detail or str(e)).lower()
            if "responsemimetype" in text or "response_mime_type" in text:
                gen.pop("responseMimeType", None)
                data = self._post(path, body)
            else:
                raise
        try:
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought")).strip()
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.spec.label}: unexpected response: {str(data)[:200]}") from e
        if not text and cand.get("finishReason") == "MAX_TOKENS":
            raise ProviderError(f"{self.spec.label} model used its whole token budget thinking; try a non-thinking model")
        return text

    def embed(self, texts: list[str], kind: str = "document", batch_size: int = 100) -> list[list[float]]:
        model = _model_path(self.spec.model)
        task = "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            reqs = [{"model": model, "content": {"parts": [{"text": t}]}, "taskType": task} for t in texts[i : i + batch_size]]
            data = self._post(f"{model}:batchEmbedContents", {"requests": reqs})
            try:
                out.extend(e["values"] for e in data["embeddings"])
            except (KeyError, TypeError) as e:
                raise ProviderError(f"{self.spec.label}: unexpected embeddings response: {str(data)[:200]}") from e
        return out
