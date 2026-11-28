"""Provider adapters against a local fake HTTP server, plus config resolution."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from recall.config import Config
from recall.llm import LLMClient
from recall.providers import AuthError, ProviderError, RateLimitError, Spec, UnavailableError, make_adapter
from recall.providers import resolve as R

KEY = "sk-test-SECRET-1234"


class FakeServer:
    """Records requests; `routes[(method, path)]` is a list of (status, body) replies used in order."""

    def __init__(self):
        self.requests: list[dict] = []
        self.routes: dict[tuple[str, str], list[tuple[int, dict]]] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                server.requests.append({"method": method, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                replies = server.routes.get((method, self.path.split("?")[0])) or [(404, {"error": "no route"})]
                status, payload = replies.pop(0) if len(replies) > 1 else replies[0]
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def spec(provider: str, base: str, model: str = "m1", **kw) -> Spec:
    from recall.providers.catalog import get

    info = get(provider)
    return Spec(provider=provider, label=info.label, base_url=base, api_key=KEY, model=model, timeout=5,
                json_mode=info.json_mode, headers=dict(info.headers), **kw)


# ---- OpenAI-compatible -----------------------------------------------------------------------------

def test_openai_chat_request_and_json_mode(server):
    server.routes[("POST", "/v1/chat/completions")] = [(200, {"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}]})]
    a = make_adapter(spec("openai", server.url + "/v1", "gpt-x"))
    assert a.chat("sys", "hi", max_tokens=50, json_mode=True) == '{"a": 1}'
    req = server.requests[-1]
    assert req["headers"]["authorization"] == f"Bearer {KEY}"
    assert req["body"]["model"] == "gpt-x"
    assert req["body"]["max_completion_tokens"] == 50 and "max_tokens" not in req["body"]
    assert req["body"]["response_format"] == {"type": "json_object"}
    assert req["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_rejected_optional_param_is_dropped_and_retried(server):
    server.routes[("POST", "/v1/chat/completions")] = [
        (400, {"error": {"message": "Unsupported parameter: 'temperature' is not supported with this model."}}),
        (200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}),
    ]
    a = make_adapter(spec("openai", server.url + "/v1"))
    assert a.chat("s", "u") == "ok"
    assert "temperature" in server.requests[0]["body"] and "temperature" not in server.requests[1]["body"]


def test_lmstudio_sends_reasoning_effort_and_strips_think(server):
    server.routes[("POST", "/v1/chat/completions")] = [(200, {"choices": [{"message": {"content": "<think>hmm</think> done"}}]})]
    a = make_adapter(spec("lmstudio", server.url + "/v1", reasoning_effort="none"))
    assert a.chat("s", "u") == "done"
    body = server.requests[-1]["body"]
    assert body["reasoning_effort"] == "none" and body["max_tokens"] == 800


def test_cloud_provider_does_not_send_reasoning_effort(server):
    server.routes[("POST", "/v1/chat/completions")] = [(200, {"choices": [{"message": {"content": "x"}}]})]
    make_adapter(spec("groq", server.url + "/v1", reasoning_effort="none")).chat("s", "u")
    assert "reasoning_effort" not in server.requests[-1]["body"]


def test_openrouter_attribution_headers(server):
    server.routes[("POST", "/v1/chat/completions")] = [(200, {"choices": [{"message": {"content": "x"}}]})]
    make_adapter(spec("openrouter", server.url + "/v1")).chat("s", "u")
    assert "x-title" in server.requests[-1]["headers"]


def test_openai_embeddings_batched_and_ordered(server):
    def reply(n):
        return (200, {"data": [{"index": i, "embedding": [float(i)]} for i in reversed(range(n))]})

    server.routes[("POST", "/v1/embeddings")] = [reply(64), reply(6)]
    vecs = make_adapter(spec("openai", server.url + "/v1", "emb")).embed([f"t{i}" for i in range(70)])
    assert len(vecs) == 70 and vecs[0] == [0.0] and vecs[64] == [0.0] and vecs[63] == [63.0]
    assert [len(r["body"]["input"]) for r in server.requests] == [64, 6]


def test_nomic_prefixes(server):
    server.routes[("POST", "/v1/embeddings")] = [(200, {"data": [{"index": 0, "embedding": [1.0]}]})]
    make_adapter(spec("lmstudio", server.url + "/v1", "text-embedding-nomic-embed-text-v1.5")).embed(["x"], kind="query")
    assert server.requests[-1]["body"]["input"] == ["search_query: x"]


def test_models_listing(server):
    server.routes[("GET", "/v1/models")] = [(200, {"data": [{"id": "b"}, {"id": "a"}]})]
    assert make_adapter(spec("mistral", server.url + "/v1")).models() == ["a", "b"]


# ---- Anthropic -------------------------------------------------------------------------------------

def test_anthropic_messages(server):
    server.routes[("POST", "/v1/messages")] = [(200, {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"})]
    a = make_adapter(spec("anthropic", server.url, "claude-x"))
    assert a.chat("be brief", "hi", max_tokens=99) == "hello"
    req = server.requests[-1]
    assert req["headers"]["x-api-key"] == KEY and req["headers"]["anthropic-version"]
    assert "authorization" not in req["headers"]
    assert req["body"]["system"] == "be brief" and req["body"]["max_tokens"] == 99
    assert req["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_models_and_no_embeddings(server):
    server.routes[("GET", "/v1/models")] = [(200, {"data": [{"id": "claude-b"}, {"id": "claude-a"}]})]
    a = make_adapter(spec("anthropic", server.url))
    assert a.models() == ["claude-a", "claude-b"]
    with pytest.raises(ProviderError, match="no embeddings"):
        a.embed(["x"])


# ---- Gemini ----------------------------------------------------------------------------------------

def test_gemini_chat_json(server):
    server.routes[("POST", "/v1beta/models/gem-x:generateContent")] = [
        (200, {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": '{"k": 2}'}]}, "finishReason": "STOP"}]})
    ]
    a = make_adapter(spec("gemini", server.url + "/v1beta", "gem-x"))
    assert a.chat("sys", "u", max_tokens=10, json_mode=True) == '{"k": 2}'
    req = server.requests[-1]
    assert req["headers"]["x-goog-api-key"] == KEY
    assert req["body"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert req["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert req["body"]["generationConfig"]["maxOutputTokens"] == 10


def test_gemini_embeddings_task_type(server):
    server.routes[("POST", "/v1beta/models/gemini-embedding-001:batchEmbedContents")] = [
        (200, {"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]})
    ]
    vecs = make_adapter(spec("gemini", server.url + "/v1beta", "gemini-embedding-001")).embed(["a", "b"], kind="query")
    assert vecs == [[0.1, 0.2], [0.3, 0.4]]
    reqs = server.requests[-1]["body"]["requests"]
    assert reqs[0]["taskType"] == "RETRIEVAL_QUERY" and reqs[0]["model"] == "models/gemini-embedding-001"


def test_gemini_models_paginated(server):
    server.routes[("GET", "/v1beta/models")] = [
        (200, {"models": [{"name": "models/b"}], "nextPageToken": "p2"}),
        (200, {"models": [{"name": "models/a"}]}),
    ]
    assert make_adapter(spec("gemini", server.url + "/v1beta")).models() == ["a", "b"]


# ---- errors ----------------------------------------------------------------------------------------

def test_401_is_auth_error_without_leaking_key(server):
    server.routes[("POST", "/v1/chat/completions")] = [(401, {"error": f"Incorrect API key provided: {KEY}"})]
    with pytest.raises(AuthError) as e:
        make_adapter(spec("openai", server.url + "/v1")).chat("s", "u")
    assert KEY not in str(e.value) and "OpenAI rejected the API key" in str(e.value)


def test_429_is_rate_limit(server):
    server.routes[("POST", "/v1/messages")] = [(429, {"error": "slow down"})]
    with pytest.raises(RateLimitError):
        make_adapter(spec("anthropic", server.url)).chat("s", "u")


def test_connection_refused_is_unavailable():
    with pytest.raises(UnavailableError, match="unreachable"):
        make_adapter(spec("openai", "http://127.0.0.1:9/v1")).models(timeout=2)


# ---- resolution ------------------------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch):
    for var in ["RECALL_CHAT_PROVIDER", "RECALL_EMBED_PROVIDER", "RECALL_CHAT_MODEL", "RECALL_EMBED_MODEL",
                "RECALL_LLM_BASE_URL", "RECALL_LLM_API_KEY", "OPENAI_API_KEY", "RECALL_OPENAI_API_KEY",
                "RECALL_OPENAI_BASE_URL", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def load(tmp_path, toml: str = "") -> Config:
    from recall.config import load_config

    p = tmp_path / "config.toml"
    p.write_text(toml)
    return load_config(p)


def test_defaults_are_lmstudio_like_0_1(clean_env, tmp_path):
    cfg = load(tmp_path)
    c, e = R.chat_spec(cfg), R.embed_spec(cfg)
    assert c.provider == e.provider == "lmstudio"
    assert e.base_url == "http://localhost:1234/v1" and e.model == "text-embedding-nomic-embed-text-v1.5"
    assert R.embed_id(e) == "text-embedding-nomic-embed-text-v1.5"  # same Chroma collection as 0.1
    assert c.api_key == "lm-studio" and c.reasoning_effort == "none"


def test_legacy_llm_base_url_still_points_lmstudio(clean_env, tmp_path):
    clean_env.setenv("RECALL_LLM_BASE_URL", "http://gpu-box:1234")
    v = R.base_url(load(tmp_path), "lmstudio")
    assert v.value == "http://gpu-box:1234/v1" and v.source == "env"


def test_precedence_env_over_file_over_default(clean_env, tmp_path):
    cfg = load(tmp_path, 'chat_provider = "openai"\nchat_model = "gpt-file"\n[providers.openai]\napi_key = "file-key"\n')
    s = R.chat_spec(cfg)
    assert s.provider == "openai" and s.model == "gpt-file" and s.api_key == "file-key"
    assert R.api_key(cfg, "openai").source == "file"
    clean_env.setenv("OPENAI_API_KEY", "env-key")
    clean_env.setenv("RECALL_CHAT_MODEL", "gpt-env")
    cfg = load(tmp_path, 'chat_provider = "openai"\nchat_model = "gpt-file"\n[providers.openai]\napi_key = "file-key"\n')
    s = R.chat_spec(cfg)
    assert s.api_key == "env-key" and s.model == "gpt-env"
    assert R.api_key(cfg, "openai").source == "env"


class FakeSettings:
    def __init__(self, values=None, secrets=None):
        self.values, self.secrets = values or {}, secrets or {}

    def get(self, key):
        return self.values.get(key)

    def secret(self, provider):
        return self.secrets.get(provider)


def test_saved_settings_sit_between_env_and_file(clean_env, tmp_path):
    cfg = load(tmp_path, 'chat_provider = "groq"\n')
    saved = FakeSettings({"chat_provider": "anthropic", "chat_model": "claude-x"}, {"anthropic": "saved-key"})
    s = R.chat_spec(cfg, saved)
    assert (s.provider, s.model, s.api_key) == ("anthropic", "claude-x", "saved-key")
    clean_env.setenv("RECALL_CHAT_PROVIDER", "openai")
    assert R.chat_spec(load(tmp_path, 'chat_provider = "groq"\n'), saved).provider == "openai"


def test_embed_provider_without_embeddings_is_rejected(clean_env, tmp_path):
    clean_env.setenv("RECALL_EMBED_PROVIDER", "anthropic")
    with pytest.raises(ProviderError, match="no embeddings API"):
        R.embed_spec(load(tmp_path))


def test_cloud_embed_id_is_namespaced_and_has_default_model(clean_env, tmp_path):
    clean_env.setenv("RECALL_EMBED_PROVIDER", "openai")
    s = R.embed_spec(load(tmp_path))
    assert s.model == "text-embedding-3-small" and R.embed_id(s) == "openai:text-embedding-3-small"


def test_cloud_chat_without_model_asks_to_choose(clean_env, tmp_path):
    clean_env.setenv("RECALL_CHAT_PROVIDER", "anthropic")
    with pytest.raises(ProviderError, match="Choose a chat model"):
        LLMClient(load(tmp_path)).chat_model


def test_unknown_provider_is_a_provider_error(clean_env, tmp_path):
    clean_env.setenv("RECALL_CHAT_PROVIDER", "nope")
    lm = LLMClient(load(tmp_path))
    with pytest.raises(ProviderError, match="Unknown provider"):
        lm.chat_spec
    assert lm.chat_available() is False


def test_local_provider_auto_picks_chat_model(clean_env, tmp_path, server):
    server.routes[("GET", "/v1/models")] = [(200, {"data": [{"id": "nomic-embed"}, {"id": "qwen-chat"}]})]
    clean_env.setenv("RECALL_LLM_BASE_URL", server.url)
    assert LLMClient(load(tmp_path)).chat_model == "qwen-chat"


def test_custom_provider_needs_base_url(clean_env, tmp_path):
    clean_env.setenv("RECALL_CHAT_PROVIDER", "custom")
    with pytest.raises(ProviderError, match="no base URL"):
        R.chat_spec(load(tmp_path))
    clean_env.setenv("RECALL_CUSTOM_BASE_URL", "https://llm.example.com/openai/v1/")
    assert R.chat_spec(load(tmp_path)).base_url == "https://llm.example.com/openai/v1"


def test_missing_key_fails_before_any_request(clean_env, tmp_path):
    clean_env.setenv("RECALL_EMBED_PROVIDER", "gemini")
    lm = LLMClient(load(tmp_path))
    with pytest.raises(ProviderError, match="No API key for Google Gemini.*GEMINI_API_KEY"):
        lm.embed(["x"])
    assert lm.available() is False


def test_error_message_is_extracted_from_json_body(server):
    server.routes[("POST", "/v1/chat/completions")] = [
        (500, {"error": {"message": "model   overloaded\n try later", "type": "server_error"}})
    ]
    with pytest.raises(ProviderError, match=r"HTTP 500: model overloaded try later$"):
        make_adapter(spec("openai", server.url + "/v1")).chat("s", "u")
