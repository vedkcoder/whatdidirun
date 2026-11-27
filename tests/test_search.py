import json
import time

from recall.importers import import_bash_history, parse_bash_history
from recall.ingest import ingest
from recall.search import Searcher, rrf


def write_spool(cfg, recs):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.spool_path, "a") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")


def rec(cmd, cwd="/home/u", exit=0, ago=0.0):
    t = time.time() - ago
    return {"cmd": cmd, "cwd": cwd, "exit": exit, "start": f"{t:.6f}", "end": f"{t + 0.5:.6f}", "host": "h", "session": "s"}


def test_rrf_prefers_agreement():
    scores, sources = rrf({"bm25": [1, 2, 3], "vector": [3, 1, 4]})
    assert max(scores, key=scores.get) in (1, 3)
    assert scores[1] > scores[2] and scores[3] > scores[4]
    assert sources[1] == ["bm25", "vector"]


def test_parse_bash_history_timestamps():
    entries = parse_bash_history("#1700000000\nls -la\nplain\n#1700000100\ngit status\n")
    assert entries == [(1700000000.0, "ls -la"), (None, "plain"), (1700000100.0, "git status")]


def test_import_idempotent(conn, tmp_path):
    h = tmp_path / "hist"
    h.write_text("#1700000000\ndocker ps\n#1700000001\ndocker ps\nexport TOKEN=abc\n")
    assert import_bash_history(conn, h, []) == (3, 0)
    assert import_bash_history(conn, h, []) == (0, 3)
    cmds = [r[0] for r in conn.execute("SELECT cmd FROM commands")]
    assert "export TOKEN=<REDACTED>" in cmds


def test_ingest_end_to_end(cfg, conn, fake_llm, tmp_path):
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    write_spool(cfg, [
        rec("ls -lS --block-size=M", ago=3600),
        rec("du -sh * | sort -h", ago=7200),
        rec("docker build -t app .", cwd=str(repo), exit=1, ago=60),
        rec("docker build -t app .", cwd=str(repo), exit=0, ago=30),
        rec("export AWS_SECRET_ACCESS_KEY=supersecret"),
        rec(" echo private"),  # leading space -> not recorded
        rec("git status", cwd=str(repo / "sub")),
        rec("df -h /"),
    ])
    res = ingest(cfg, conn)
    assert (res.stored, res.skipped) == (7, 1)
    assert res.described == 6 and res.embedded == 6  # docker build deduped into one text
    assert not cfg.spool_path.exists()

    row = conn.execute("SELECT git_repo, git_branch FROM commands WHERE cmd = 'git status'").fetchone()
    assert tuple(row) == ("myrepo", "main")
    assert not conn.execute("SELECT 1 FROM commands WHERE cmd LIKE '%supersecret%'").fetchone()

    s = Searcher(cfg, conn)
    r = s.search("list files sorted by size")
    assert r.mode == "hybrid"
    assert r.results[0].cmd in {"ls -lS --block-size=M", "du -sh * | sort -h"}

    r = s.search("docker that failed in myrepo")
    assert r.filters["failed"] is True and r.filters["repo"] == "myrepo"
    assert [x.cmd for x in r.results] == ["docker build -t app ."]
    assert r.results[0].runs == 2 and r.results[0].failures == 1

    r = s.search("what failed")
    assert r.mode == "recent" and [x.cmd for x in r.results] == ["docker build -t app ."]

    # "free disk space" shares no tokens with "df -h /" -- only the LLM description connects them
    r = s.search("how much free disk space is left", refresh=False)
    assert r.results[0].cmd == "df -h /"
    assert r.results[0].description.startswith("show free")

    st = s.stats()
    assert st["total_runs"] == 7 and st["failures"] == 1 and st["pending_descriptions"] == 0


def test_degrades_to_keyword_when_lmstudio_down(cfg, conn):
    write_spool(cfg, [rec("kubectl get pods -A")])
    res = ingest(cfg, conn)
    assert res.stored == 1 and res.embedded == 0 and res.described == 0 and res.warnings
    r = Searcher(cfg, conn).search("kubectl pods")
    assert r.mode == "keyword" and r.results[0].cmd == "kubectl get pods -A"
    assert any("keyword results only" in w for w in r.warnings)


def test_web_api(cfg, conn, fake_llm):
    from fastapi.testclient import TestClient

    from recall.web.api import create_app

    write_spool(cfg, [rec("tar -czvf backup.tgz ./data")])
    ingest(cfg, conn)
    client = TestClient(create_app(cfg, background_indexer=False))
    data = client.get("/api/search", params={"q": "compress a folder backup"}).json()
    assert data["results"][0]["cmd"] == "tar -czvf backup.tgz ./data"
    tid = data["results"][0]["text_id"]
    detail = client.get(f"/api/command/{tid}").json()
    assert len(detail["runs"]) == 1 and detail["runs"][0]["exit_code"] == 0
    assert client.get("/api/stats").json()["total_runs"] == 1


def test_describe_failure_marks_and_continues(cfg, conn, fake_lm, monkeypatch):
    from recall.lmstudio import LMStudio, LMStudioError

    def bad(self, system, user, max_tokens=800):
        raise LMStudioError("nope")

    monkeypatch.setattr(LMStudio, "chat_json", bad)
    write_spool(cfg, [rec("echo one"), rec("echo two"), rec("echo three")])
    res = ingest(cfg, conn)
    assert res.described == 0 and res.embedded == 3  # still embedded without descriptions
    assert {r[0] for r in conn.execute("SELECT described FROM cmd_texts")} == {-1}


def test_lm_check_runs_on_first_use_right_after_boot(cfg, conn, monkeypatch):
    """monotonic() is time since boot; the first availability check must not be skipped on a fresh machine."""
    from recall import search as search_mod
    from recall.lmstudio import LMStudio

    monkeypatch.setattr(search_mod.time, "monotonic", lambda: 5.0)  # booted 5 s ago
    monkeypatch.setattr(LMStudio, "available", lambda self: True)
    assert Searcher(cfg, conn).lm_available()
