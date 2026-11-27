"""Client for the LM Studio server (OpenAI-compatible API): embeddings + chat.

Uses the stdlib instead of the `openai` SDK: we need two endpoints, and the SDK alone
adds ~1.8s of import time to every CLI invocation.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from functools import cached_property

from .config import Config

_THINK = re.compile(r"<think>.*?</think>", re.S)


class LMStudioError(RuntimeError):
    pass


class LMStudio:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        req = urllib.request.Request(
            self.cfg.base_url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.llm_api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.cfg.llm_timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise LMStudioError(f"LM Studio {path} returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            raise LMStudioError(f"LM Studio request {path} failed: {e}") from e

    # ---- health -----------------------------------------------------------------
    def models(self, timeout: float = 3.0) -> list[str]:
        try:
            return [m["id"] for m in self._request("GET", "/models", timeout=timeout)["data"]]
        except (LMStudioError, KeyError, TypeError) as e:
            raise LMStudioError(f"LM Studio unreachable at {self.cfg.base_url}: {e}") from e

    def available(self) -> bool:
        try:
            self.models(timeout=2.0)
            return True
        except LMStudioError:
            return False

    @cached_property
    def chat_model(self) -> str:
        if self.cfg.chat_model:
            return self.cfg.chat_model
        for m in self.models():
            if "embed" not in m.lower():
                return m
        raise LMStudioError("No chat model loaded in LM Studio; set RECALL_CHAT_MODEL")

    # ---- embeddings -------------------------------------------------------------
    def _prefix(self, kind: str) -> str:
        # nomic-embed models are trained with task prefixes and work noticeably better with them.
        if "nomic" in self.cfg.embed_model.lower():
            return "search_query: " if kind == "query" else "search_document: "
        return ""

    def embed(self, texts: list[str], kind: str = "document", batch_size: int = 64) -> list[list[float]]:
        prefix = self._prefix(kind)
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = [prefix + t for t in texts[i : i + batch_size]]
            data = self._request("POST", "/embeddings", {"model": self.cfg.embed_model, "input": batch})
            try:
                out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
            except (KeyError, TypeError) as e:
                raise LMStudioError(f"Unexpected embeddings response: {str(data)[:200]}") from e
        return out

    # ---- chat -------------------------------------------------------------------
    def chat(self, system: str, user: str, max_tokens: int = 800) -> str:
        if not self.cfg.llm_enabled:
            raise LMStudioError("LLM features disabled (RECALL_LLM_ENABLED=0)")
        body = {
            "model": self.chat_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        # "none" stops thinking models from burning the whole budget on reasoning (see config.py).
        if self.cfg.reasoning_effort not in ("", "omit"):
            body["reasoning_effort"] = self.cfg.reasoning_effort
        data = self._request("POST", "/chat/completions", body)
        try:
            choice = data["choices"][0]
            text = _THINK.sub("", choice["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as e:
            raise LMStudioError(f"Unexpected chat response: {str(data)[:200]}") from e
        if not text and choice.get("finish_reason") == "length":
            raise LMStudioError("Model used its whole token budget thinking; try RECALL_REASONING_EFFORT=none or another model")
        return text

    def chat_json(self, system: str, user: str, max_tokens: int = 800):
        text = self.chat(system, user, max_tokens)
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise LMStudioError(f"Model did not return JSON: {text[:200]!r}")
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            raise LMStudioError(f"Model returned invalid JSON: {e}") from e
