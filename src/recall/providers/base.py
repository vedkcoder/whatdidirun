"""Shared pieces for provider adapters: errors, the HTTP helper and the adapter interfaces.

Adapters use the stdlib instead of vendor SDKs: we need a handful of endpoints, and the SDKs add
seconds of import time to every CLI invocation (and megabytes to the Windows installer).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

THINK = re.compile(r"<think>.*?</think>", re.S)


class ProviderError(RuntimeError):
    """Any failure talking to a chat or embedding provider."""


class AuthError(ProviderError):
    """The provider rejected the API key (HTTP 401/403)."""


class RateLimitError(ProviderError):
    """The provider is rate limiting us or the account is out of quota (HTTP 429)."""


class UnavailableError(ProviderError):
    """The provider could not be reached at all (connection refused, DNS, timeout)."""


class BadRequestError(ProviderError):
    """HTTP 400: usually a parameter or model name the provider doesn't accept."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


@dataclass
class Spec:
    """Everything an adapter needs: resolved from config / settings by `providers.resolve`."""

    provider: str  # catalog id, e.g. "openai"
    label: str  # human name, e.g. "OpenAI"
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout: float = 60.0
    reasoning_effort: str = ""  # sent only when non-empty and the provider accepts it
    headers: dict[str, str] = field(default_factory=dict)
    json_mode: bool = False  # provider supports a native JSON response mode


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret and len(secret) > 3 else text


def _error_detail(raw: str) -> str:
    """Pull the human message out of a provider's JSON error body; fall back to the raw text."""
    try:
        data = json.loads(raw)
    except ValueError:
        return " ".join(raw.split())[:300]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        msg = err.get("message") or err.get("msg") or err.get("type") or json.dumps(err)
    else:
        msg = str(err)
    return " ".join(str(msg).split())[:300]


def request_json(
    spec: Spec,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict | None = None,
    timeout: float | None = None,
) -> dict:
    """Send a JSON request and map failures to ProviderError subclasses (never leaking the API key)."""
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
    )
    where = url.split("?", 1)[0].replace(spec.base_url, "") or url
    try:
        with urllib.request.urlopen(req, timeout=timeout or spec.timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = _scrub(_error_detail(e.read().decode(errors="replace")[:4000]), spec.api_key)
        if e.code in (401, 403):
            raise AuthError(f"{spec.label} rejected the API key (HTTP {e.code}). Check it in Settings. {detail}") from None
        if e.code == 429:
            raise RateLimitError(f"{spec.label} is rate limiting requests or the account is out of quota (HTTP 429). {detail}") from None
        if e.code == 400:
            raise BadRequestError(f"{spec.label} {where} returned HTTP 400: {detail}", detail) from None
        raise ProviderError(f"{spec.label} {where} returned HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise UnavailableError(f"{spec.label} unreachable at {spec.base_url}: {_scrub(str(reason), spec.api_key)}") from None
    except json.JSONDecodeError as e:
        raise ProviderError(f"{spec.label} {where} returned invalid JSON: {e}") from None


def drop_rejected_params(err: BadRequestError, body: dict, optional: tuple[str, ...]) -> bool:
    """If a 400 names one of our optional parameters, remove it from `body` and return True (caller retries).

    Lets one adapter work across model families that disagree on e.g. `temperature`,
    `reasoning_effort` or `response_format`, without a table of which model supports what.
    """
    text = (err.detail or str(err)).lower()
    dropped = False
    for name in optional:
        if name in body and name.lower() in text:
            del body[name]
            dropped = True
    return dropped


class ChatAdapter(Protocol):
    spec: Spec

    def models(self, timeout: float = 5.0) -> list[str]: ...

    def chat(self, system: str, user: str, max_tokens: int = 800, json_mode: bool = False) -> str: ...


class EmbedAdapter(Protocol):
    spec: Spec

    def models(self, timeout: float = 5.0) -> list[str]: ...

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]: ...
