"""Chat and embedding providers (OpenAI, Anthropic, Gemini, OpenRouter, Groq, Ollama, LM Studio, ...)."""

from __future__ import annotations

from .base import AuthError, ProviderError, RateLimitError, Spec, UnavailableError
from .catalog import CATALOG, ProviderInfo, get


def make_adapter(spec: Spec):
    """Build the adapter for a resolved spec."""
    info = get(spec.provider)
    if info.adapter == "anthropic":
        from .anthropic import Anthropic

        return Anthropic(spec, info)
    if info.adapter == "gemini":
        from .gemini import Gemini

        return Gemini(spec, info)
    from .openai_compat import OpenAICompat

    return OpenAICompat(spec, info)


__all__ = [
    "CATALOG",
    "AuthError",
    "ProviderError",
    "ProviderInfo",
    "RateLimitError",
    "Spec",
    "UnavailableError",
    "get",
    "make_adapter",
]
