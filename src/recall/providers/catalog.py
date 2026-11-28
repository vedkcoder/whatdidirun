"""The providers recall knows about. Adding another OpenAI-compatible service is one entry here."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    adapter: str  # "openai" (OpenAI-compatible), "anthropic" or "gemini"
    base_url: str  # default; "" = the user must supply one (custom)
    chat: bool = True
    embeddings: bool = False
    needs_key: bool = True
    local: bool = False  # runs on the user's machine: auto-pick models, no privacy notice
    key_env: tuple[str, ...] = ()  # standard env vars that hold the key, checked after RECALL_<ID>_API_KEY
    key_url: str = ""  # where to create a key
    default_key: str = ""  # sent when no key is configured (LM Studio accepts any)
    default_embed_model: str = ""
    json_mode: bool = False  # supports response_format={"type": "json_object"}
    send_reasoning_effort: bool = False  # send cfg.reasoning_effort (dropped automatically if rejected)
    max_tokens_field: str = "max_tokens"
    normalize_v1: bool = False  # append /v1 to a user-supplied base URL that lacks it
    headers: dict[str, str] = field(default_factory=dict)


CATALOG: dict[str, ProviderInfo] = {
    p.id: p
    for p in [
        ProviderInfo(
            "lmstudio", "LM Studio", "openai", "http://localhost:1234/v1",
            embeddings=True, needs_key=False, local=True, default_key="lm-studio",
            default_embed_model="text-embedding-nomic-embed-text-v1.5",
            send_reasoning_effort=True, normalize_v1=True,
        ),
        ProviderInfo(
            "ollama", "Ollama", "openai", "http://localhost:11434/v1",
            embeddings=True, needs_key=False, local=True, default_key="ollama",
            default_embed_model="nomic-embed-text", normalize_v1=True,
        ),
        ProviderInfo(
            "openai", "OpenAI", "openai", "https://api.openai.com/v1",
            embeddings=True, key_env=("OPENAI_API_KEY",), key_url="https://platform.openai.com/api-keys",
            default_embed_model="text-embedding-3-small", json_mode=True, max_tokens_field="max_completion_tokens",
        ),
        ProviderInfo(
            "anthropic", "Anthropic (Claude)", "anthropic", "https://api.anthropic.com",
            key_env=("ANTHROPIC_API_KEY",), key_url="https://console.anthropic.com/settings/keys",
        ),
        ProviderInfo(
            "gemini", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta",
            embeddings=True, key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"), key_url="https://aistudio.google.com/apikey",
            default_embed_model="gemini-embedding-001", json_mode=True,
        ),
        ProviderInfo(
            "openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
            key_env=("OPENROUTER_API_KEY",), key_url="https://openrouter.ai/keys",
            headers={"HTTP-Referer": "https://github.com/vedkcoder/whatdidirun", "X-Title": "What did I Run ??"},
        ),
        ProviderInfo(
            "groq", "Groq", "openai", "https://api.groq.com/openai/v1",
            key_env=("GROQ_API_KEY",), key_url="https://console.groq.com/keys", json_mode=True,
        ),
        ProviderInfo(
            "mistral", "Mistral", "openai", "https://api.mistral.ai/v1",
            embeddings=True, key_env=("MISTRAL_API_KEY",), key_url="https://console.mistral.ai/api-keys",
            default_embed_model="mistral-embed", json_mode=True,
        ),
        ProviderInfo(
            "deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1",
            key_env=("DEEPSEEK_API_KEY",), key_url="https://platform.deepseek.com/api_keys", json_mode=True,
        ),
        ProviderInfo(
            "xai", "xAI (Grok)", "openai", "https://api.x.ai/v1",
            key_env=("XAI_API_KEY",), key_url="https://console.x.ai",
        ),
        ProviderInfo(
            "together", "Together AI", "openai", "https://api.together.xyz/v1",
            embeddings=True, key_env=("TOGETHER_API_KEY",), key_url="https://api.together.ai/settings/api-keys",
        ),
        ProviderInfo(
            "custom", "Custom (OpenAI-compatible)", "openai", "",
            embeddings=True, needs_key=False, send_reasoning_effort=True,
        ),
    ]
}


def get(provider_id: str) -> ProviderInfo:
    try:
        return CATALOG[provider_id]
    except KeyError:
        known = ", ".join(CATALOG)
        raise ValueError(f"Unknown provider {provider_id!r}. Known providers: {known}") from None
