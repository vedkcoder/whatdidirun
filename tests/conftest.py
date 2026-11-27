import hashlib
import math
import re

import pytest

from recall.config import Config
from recall.db import connect


def fake_embed(self, texts, kind="document", batch_size=64):
    """Deterministic bag-of-words embedding with a few synonyms, standing in for LM Studio."""
    synonyms = {"sort": "order", "sorted": "order", "size": "big", "largest": "big", "biggest": "big",
                "du": "big", "files": "file", "file": "file", "lss": "big"}
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in re.findall(r"\w+", t.lower()):
            w = synonyms.get(w, w)
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for var in ["RECALL_LLM_BASE_URL", "RECALL_EMBED_MODEL", "RECALL_CHAT_MODEL", "RECALL_DATA_DIR"]:
        monkeypatch.delenv(var, raising=False)
    return Config(data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", embed_model="fake-embed")


@pytest.fixture
def conn(cfg):
    return connect(cfg.db_path)


@pytest.fixture
def fake_lm(monkeypatch):
    from recall.lmstudio import LMStudio

    monkeypatch.setattr(LMStudio, "embed", fake_embed)
    monkeypatch.setattr(LMStudio, "available", lambda self: True)


DESCRIPTIONS = {
    "ls": "list directory contents sorted by file size",
    "du": "show which folders use the most disk space",
    "df": "show free and used disk space on mounted filesystems",
    "docker": "build a container image",
    "tar": "compress a folder into a backup archive",
}


def fake_chat_json(self, system, user, max_tokens=800):
    if '{"d"' in system:
        lines = [l.split(": ", 1)[1] for l in user.splitlines()]
        return {"d": [DESCRIPTIONS.get(l.split()[0], "generic shell command") for l in lines]}
    raise AssertionError("unexpected chat call")


@pytest.fixture
def fake_llm(monkeypatch, fake_lm):
    from recall.lmstudio import LMStudio

    monkeypatch.setattr(LMStudio, "chat_json", fake_chat_json)
