from datetime import datetime

from recall.query_parse import keywords, parse_query

NOW = datetime(2026, 11, 27, 15, 0)  # a Friday


def test_failed_and_repo_and_time():
    p = parse_query("docker build that failed in project1 last week", {"project1"}, set(), NOW)
    assert p.filters.failed is True
    assert p.filters.repo == "project1"
    assert p.filters.since == NOW.timestamp() - 7 * 86400
    assert p.text == "docker build that"
    assert "docker" in keywords(p.text) and "that" not in keywords(p.text)


def test_yesterday_and_dir():
    p = parse_query("ffmpeg commands in Downloads yesterday", set(), {"Downloads"}, NOW)
    assert p.filters.cwd == "Downloads"
    assert p.filters.until == datetime(2026, 11, 27).timestamp()
    assert p.filters.since == datetime(2026, 11, 26).timestamp()
    assert p.text == "ffmpeg commands"


def test_weekday():
    p = parse_query("what did I run on tuesday", now=NOW)
    assert datetime.fromtimestamp(p.filters.since) == datetime(2026, 11, 24)
    assert p.text == ""  # only filler words left -> list mode


def test_last_n_days_and_unknown_location_kept():
    p = parse_query("resize images in bulk in the last 3 days", set(), set(), NOW)
    assert p.filters.since == NOW.timestamp() - 3 * 86400
    assert p.filters.repo is None and "bulk" in p.text


def test_since_dateparser():
    p = parse_query("kubectl since 2026-11-01", now=NOW)
    assert datetime.fromtimestamp(p.filters.since).date().isoformat() == "2026-11-01"
    assert p.text == "kubectl"


def test_plain_query_untouched():
    p = parse_query("list files sorted by size", now=NOW)
    assert not p.filters.any()
    assert p.text == "list files sorted by size"
