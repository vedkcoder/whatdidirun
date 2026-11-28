"""Saved settings, encrypted API keys and the settings API (incl. its CSRF / DNS-rebinding guards)."""

from __future__ import annotations

import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from recall.keystore import KeyStore, KeyStoreError
from recall.providers import resolve as R
from recall.settings import SettingsError, SettingsStore, store_for

SECRET = "sk-test-VERY-SECRET-abcd1234"
H = {"X-Recall-Request": "1"}


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    for v in ["RECALL_SECRET_KEY", "RECALL_SETTINGS_TOKEN", "RECALL_CHAT_PROVIDER", "RECALL_EMBED_PROVIDER",
              "RECALL_CHAT_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"]:
        monkeypatch.delenv(v, raising=False)


# ---- keystore --------------------------------------------------------------------------------------

def test_keystore_roundtrip_and_private_key_file(tmp_path):
    ks = KeyStore(tmp_path, windows=False)
    nonce, ct = ks.encrypt(SECRET, "openai")
    assert SECRET.encode() not in ct
    assert ks.decrypt(nonce, ct, "openai") == SECRET
    key_file = tmp_path / "secret.key"
    assert key_file.stat().st_size == 32
    if os.name == "posix":
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    # a second instance (another process) reuses the same key
    assert KeyStore(tmp_path, windows=False).decrypt(nonce, ct, "openai") == SECRET


def test_ciphertext_is_bound_to_its_provider(tmp_path):
    ks = KeyStore(tmp_path, windows=False)
    nonce, ct = ks.encrypt(SECRET, "openai")
    with pytest.raises(KeyStoreError):
        ks.decrypt(nonce, ct, "anthropic")


def test_wrong_master_key_fails_cleanly(tmp_path, monkeypatch):
    nonce, ct = KeyStore(tmp_path, windows=False).encrypt(SECRET, "openai")
    monkeypatch.setenv("RECALL_SECRET_KEY", "a different passphrase")
    with pytest.raises(KeyStoreError, match="can't be decrypted"):
        KeyStore(tmp_path, windows=False).decrypt(nonce, ct, "openai")


def test_env_master_key_creates_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_SECRET_KEY", "my docker passphrase")
    ks = KeyStore(tmp_path / "d", windows=False)
    nonce, ct = ks.encrypt(SECRET, "x")
    assert ks.backend == "env" and not (tmp_path / "d").exists()
    assert KeyStore(tmp_path / "other", windows=False).decrypt(nonce, ct, "x") == SECRET


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_wrapped_master_key(tmp_path):
    ks = KeyStore(tmp_path, windows=True)
    nonce, ct = ks.encrypt(SECRET, "openai")
    blob = (tmp_path / "secret.key.dpapi").read_bytes()
    assert len(blob) > 32 and ks.key not in blob  # stored wrapped, not raw
    assert KeyStore(tmp_path, windows=True).decrypt(nonce, ct, "openai") == SECRET


# ---- settings store --------------------------------------------------------------------------------

def test_secret_never_stored_in_plain_text(cfg):
    st = SettingsStore(cfg)
    st.set_secret("openai", SECRET)
    assert st.secret("openai") == SECRET and st.hint("openai") == "…1234"
    st.conn.execute("PRAGMA wal_checkpoint(FULL)")
    for f in cfg.data_dir.iterdir():
        if f.is_file():
            assert SECRET.encode() not in f.read_bytes(), f.name


def test_validation(cfg):
    st = SettingsStore(cfg)
    with pytest.raises(SettingsError, match="Unknown provider"):
        st.set("chat_provider", "nope")
    with pytest.raises(SettingsError, match="no embeddings API"):
        st.set("embed_provider", "anthropic")
    with pytest.raises(SettingsError, match="unknown setting"):
        st.set("web_host", "0.0.0.0")
    with pytest.raises(SettingsError, match="http"):
        st.set("provider.ollama.base_url", "file:///etc/passwd")
    with pytest.raises(SettingsError):
        st.set_secret("openai", "two words")
    st.set("llm_enabled", "off")
    assert st.get("llm_enabled") == "0"
    st.set("chat_model", "")
    assert st.get("chat_model") == ""  # "" = auto (beats a model from config.toml)
    st.set("chat_model", None)
    assert st.get("chat_model") is None
    st.set("provider.ollama.base_url", "")
    assert st.get("provider.ollama.base_url") is None


def test_saved_settings_drive_resolution(cfg):
    st = store_for(cfg)
    st.update({"chat_provider": "anthropic", "chat_model": "claude-x", "embed_provider": "openai", "embed_model": ""})
    st.set_secret("anthropic", SECRET)
    c = R.chat_spec(cfg, st)
    assert (c.provider, c.model, c.api_key) == ("anthropic", "claude-x", SECRET)
    assert R.embed_spec(cfg, st).model == "text-embedding-3-small"
    assert R.chat_provider(cfg, st).source == "settings"


def test_searcher_notices_changes_from_another_process(cfg, conn):
    from recall.search import Searcher

    s = Searcher(cfg, conn)
    assert s.lm.chat_spec.provider == "lmstudio"
    SettingsStore(cfg).set("chat_provider", "groq")  # separate connection, like the CLI while the tray runs
    assert s.lm.chat_spec.provider == "groq"


def test_custom_endpoints(cfg):
    st = SettingsStore(cfg)
    pid = st.add_custom("GPU box", "http://gpu-box:8000/v1/", embeddings=False, api_key="k-123456789")
    assert pid == "custom-gpu-box"
    assert st.add_custom("GPU box", "http://other:8000/v1") == "custom-gpu-box-2"
    st.set("chat_provider", pid)
    spec = R.chat_spec(cfg, st)
    assert spec.base_url == "http://gpu-box:8000/v1" and spec.api_key == "k-123456789"
    with pytest.raises(SettingsError, match="no embeddings"):
        st.set("embed_provider", pid)
    st.update_custom(pid, base_url="http://gpu-box:9000/v1")
    assert R.chat_spec(cfg, st).base_url == "http://gpu-box:9000/v1"
    st.remove_custom(pid)
    assert st.get("chat_provider") is None and st.secret(pid) is None  # nothing left pointing at it


def test_custom_endpoint_env_key_name(cfg, monkeypatch):
    st = SettingsStore(cfg)
    pid = st.add_custom("My Server", "http://s:1/v1")
    monkeypatch.setenv("RECALL_CUSTOM_MY_SERVER_API_KEY", "from-env")
    assert R.api_key(cfg, pid, st).value == "from-env"


# ---- API -------------------------------------------------------------------------------------------

@pytest.fixture
def client(cfg):
    from recall.web.api import create_app

    return TestClient(create_app(cfg, background_indexer=False), base_url="http://127.0.0.1:8765")


def test_settings_require_header_and_loopback_host(client):
    assert client.get("/api/settings").status_code == 403
    assert client.get("/api/settings", headers=H).status_code == 200
    assert client.get("/api/settings", headers={**H, "Host": "evil.example:8765"}).status_code == 403
    assert client.get("/api/settings", headers={**H, "Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/settings", headers={**H, "Origin": "http://127.0.0.1:8765"}).status_code == 200
    assert client.put("/api/settings/keys/openai", json={"api_key": SECRET}).status_code == 403


def test_key_is_never_returned_except_by_reveal(client):
    assert client.put("/api/settings/keys/openai", json={"api_key": SECRET}, headers=H).json()["hint"] == "…1234"
    body = client.get("/api/settings", headers=H).text
    assert SECRET not in body
    openai = next(p for p in client.get("/api/settings", headers=H).json()["providers"] if p["id"] == "openai")
    assert openai["key"] == {"set": True, "source": "settings", "saved": True, "hint": "…1234", "error": None}
    assert client.post("/api/settings/keys/openai/reveal", headers=H).json() == {"api_key": SECRET}
    assert client.delete("/api/settings/keys/openai", headers=H).json() == {"ok": True}
    assert client.post("/api/settings/keys/openai/reveal", headers=H).status_code == 404


def test_env_key_shows_as_env_source(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    groq = next(p for p in client.get("/api/settings", headers=H).json()["providers"] if p["id"] == "groq")
    assert groq["key"]["set"] and groq["key"]["source"] == "env" and not groq["key"]["saved"]
    assert SECRET not in client.get("/api/settings", headers=H).text


def test_saving_provider_takes_effect_without_restart(client):
    assert client.get("/api/health").json()["chat_provider"] == "LM Studio"
    r = client.put("/api/settings", json={"values": {"chat_provider": "anthropic", "chat_model": "claude-x"}}, headers=H)
    assert r.status_code == 200 and r.json()["values"]["chat_provider"] == {"value": "anthropic", "source": "settings"}
    assert client.get("/api/health").json()["chat_provider"] == "Anthropic (Claude)"
    bad = client.put("/api/settings", json={"values": {"embed_provider": "groq"}}, headers=H)
    assert bad.status_code == 400 and "no embeddings" in bad.json()["detail"]


def test_network_bound_server_is_read_only_without_token(cfg, monkeypatch):
    from recall.web.api import create_app

    cfg.web_host = "0.0.0.0"
    c = TestClient(create_app(cfg, background_indexer=False), base_url="http://192.168.1.20:8765")
    s = c.get("/api/settings", headers=H)
    assert s.status_code == 200 and s.json()["writable"] is False
    assert c.put("/api/settings/keys/openai", json={"api_key": SECRET}, headers=H).status_code == 403

    monkeypatch.setenv("RECALL_SETTINGS_TOKEN", "tok-123")
    assert c.put("/api/settings/keys/openai", json={"api_key": SECRET}, headers=H).status_code == 401
    ok = c.put("/api/settings/keys/openai", json={"api_key": SECRET}, headers={**H, "X-Recall-Token": "tok-123"})
    assert ok.status_code == 200


def test_test_button_and_models_against_fake_provider(client):
    from test_providers import FakeServer

    srv = FakeServer()
    try:
        srv.routes[("GET", "/v1/models")] = [(200, {"data": [{"id": "embed-a"}, {"id": "chat-b"}]})]
        srv.routes[("POST", "/v1/chat/completions")] = [(200, {"choices": [{"message": {"content": "ok"}}]})]
        srv.routes[("POST", "/v1/embeddings")] = [(200, {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})]
        pid = client.post("/api/settings/custom", json={"label": "Fake", "base_url": srv.url + "/v1"}, headers=H).json()["id"]
        assert client.post(f"/api/providers/{pid}/models", json={}, headers=H).json() == {"models": ["chat-b", "embed-a"]}
        chat = client.post("/api/settings/test", json={"kind": "chat", "provider": pid}, headers=H).json()
        assert chat["ok"] and chat["model"] == "chat-b" and chat["reply"] == "ok"
        emb = client.post("/api/settings/test", json={"kind": "embed", "provider": pid, "model": "embed-a"}, headers=H).json()
        assert emb == {"ok": True, "model": "embed-a", "dims": 3, "ms": emb["ms"]}
        # unsaved key typed into the form is used for the test
        srv.routes[("POST", "/v1/chat/completions")] = [(200, {"choices": [{"message": {"content": "ok"}}]})]
        client.post("/api/settings/test", json={"kind": "chat", "provider": pid, "model": "m", "api_key": "typed-key-999"}, headers=H)
        assert srv.requests[-1]["headers"]["authorization"] == "Bearer typed-key-999"
    finally:
        srv.close()


def test_test_button_reports_missing_key(client):
    r = client.post("/api/settings/test", json={"kind": "chat", "provider": "openai", "model": "gpt-x"}, headers=H).json()
    assert r["ok"] is False and "No API key for OpenAI" in r["error"]


def test_ingest_honours_saved_ai_switch(cfg, conn, fake_llm):
    from test_search import rec, write_spool

    from recall.ingest import ingest

    store_for(cfg).set("llm_enabled", "off")
    write_spool(cfg, [rec("echo hi")])
    res = ingest(cfg, conn)  # no client passed, like the background indexer
    assert res.described == 0 and res.embedded == 1
