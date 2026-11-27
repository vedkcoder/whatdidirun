"""Pull structured filters (time, exit status, repo/dir) out of a natural-language query."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

_UNITS = {"minute": 60, "min": 60, "hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}
_UNIT_RE = r"(minute|min|hour|day|week|month|year)s?"
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

STOPWORDS = set(
    """a an the i me my we you it its is was were be been am are do did does done how what which who when where why
    that this those these there here to of in on at for from by with about into onto as and or but not no so if then
    than can could would should will shall may might must use used using ran run running command commands cmd thing since after
    stuff something some any find show list give get got tell remember again last time one ones please""".split()
)
# Words that carry no meaning on their own; if only these remain, we list recent commands instead of searching.
_FILLER = STOPWORDS | {"all", "everything", "recent", "recently", "history", "earlier", "before", "previous"}


@dataclass
class Filters:
    since: float | None = None
    until: float | None = None
    failed: bool | None = None
    repo: str | None = None
    cwd: str | None = None

    def any(self) -> bool:
        return any(v is not None for v in asdict(self).values())


@dataclass
class ParsedQuery:
    raw: str
    text: str  # what remains for keyword/semantic search ('' = list recent matching filters)
    filters: Filters = field(default_factory=Filters)
    notes: list[str] = field(default_factory=list)  # human-readable description of extracted filters


def _midnight(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _time_filters(q: str, now: datetime, f: Filters, notes: list[str]) -> str:
    def cut(m: re.Match) -> str:
        return " "

    rules: list[tuple[str, callable]] = []

    def rule(pattern: str):
        def deco(fn):
            rules.append((pattern, fn))
            return fn
        return deco

    @rule(r"\btoday\b")
    def _(m):
        f.since = _midnight(now).timestamp(); notes.append("today")

    @rule(r"\byesterday\b")
    def _(m):
        f.since = (_midnight(now) - timedelta(days=1)).timestamp(); f.until = _midnight(now).timestamp(); notes.append("yesterday")

    @rule(rf"\b(?:in\s+the\s+)?(?:last|past|previous)\s+(\d+)\s+{_UNIT_RE}\b")
    def _(m):
        n, unit = int(m.group(1)), m.group(2)
        f.since = now.timestamp() - n * _UNITS[unit]; notes.append(f"last {n} {unit}s")

    @rule(rf"\b(\d+)\s+{_UNIT_RE}\s+ago\b")
    def _(m):
        n, unit = int(m.group(1)), m.group(2)
        span = _UNITS[unit]
        f.since = now.timestamp() - (n + 1) * span; f.until = now.timestamp() - max(n - 1, 0) * span
        notes.append(f"~{n} {unit}s ago")

    @rule(rf"\b(?:in\s+the\s+)?(?:last|past)\s+{_UNIT_RE}\b")
    def _(m):
        unit = m.group(1)
        f.since = now.timestamp() - _UNITS[unit]; notes.append(f"last {unit}")

    @rule(r"\bthis\s+(week|month|year)\b")
    def _(m):
        unit = m.group(1)
        start = _midnight(now)
        if unit == "week":
            start -= timedelta(days=now.weekday())
        elif unit == "month":
            start = start.replace(day=1)
        else:
            start = start.replace(month=1, day=1)
        f.since = start.timestamp(); notes.append(f"this {unit}")

    @rule(rf"\b(?:on\s+|last\s+)?({'|'.join(_WEEKDAYS)})\b")
    def _(m):
        target = _WEEKDAYS.index(m.group(1))
        delta = (now.weekday() - target) % 7 or 7
        day = _midnight(now) - timedelta(days=delta)
        f.since = day.timestamp(); f.until = (day + timedelta(days=1)).timestamp(); notes.append(m.group(1))

    @rule(r"\b(since|after|before)\s+(.+?)(?=\s+(?:in|inside|that|which|with|where|and)\b|$)")
    def _(m):
        import dateparser  # slow import; only when needed

        dt = dateparser.parse(m.group(2), settings={"PREFER_DATES_FROM": "past", "RELATIVE_BASE": now})
        if dt is None:
            raise ValueError
        if m.group(1) == "before":
            f.until = dt.timestamp()
        else:
            f.since = dt.timestamp()
        notes.append(f"{m.group(1)} {dt:%Y-%m-%d %H:%M}")

    for pattern, fn in rules:
        m = re.search(pattern, q, re.I)
        if not m:
            continue
        try:
            fn(m)
        except ValueError:
            continue
        q = q[: m.start()] + cut(m) + q[m.end() :]
    return q


_FAILED = re.compile(r"\b(failed|failing|fails|errored|erroring|crashed|broke|didn'?t\s+work|did\s+not\s+work|non-?zero|unsuccessful)\b", re.I)
_SUCCEEDED = re.compile(r"\b(succeeded|successful(?:ly)?|worked|passed|that\s+works?)\b", re.I)
_LOCATION = re.compile(
    r"\b(?:in|inside|within|from|under)\s+(?:the\s+|my\s+)?(?:(repo(?:sitory)?|project|dir(?:ectory)?|folder)\s+)?[\"']?([\w.-]+)[\"']?(?:\s+(?:repo(?:sitory)?|project|dir(?:ectory)?|folder))?",
    re.I,
)


def parse_query(raw: str, known_repos: set[str] = frozenset(), known_dirs: set[str] = frozenset(), now: datetime | None = None) -> ParsedQuery:
    now = now or datetime.now()
    f = Filters()
    notes: list[str] = []
    q = _time_filters(raw, now, f, notes)

    if m := _FAILED.search(q):
        f.failed = True; notes.append("failed"); q = q[: m.start()] + " " + q[m.end() :]
    elif m := _SUCCEEDED.search(q):
        f.failed = False; notes.append("succeeded"); q = q[: m.start()] + " " + q[m.end() :]

    repos_lc = {r.lower(): r for r in known_repos}
    dirs_lc = {d.lower(): d for d in known_dirs}
    for m in _LOCATION.finditer(q):
        name = m.group(2).lower()
        if name in repos_lc:
            f.repo = repos_lc[name]; notes.append(f"repo {f.repo}")
        elif name in dirs_lc:
            f.cwd = dirs_lc[name]; notes.append(f"dir {f.cwd}")
        else:
            continue
        q = q[: m.start()] + " " + q[m.end() :]
        break

    q = re.sub(r"\s+", " ", q).strip(" ?.,!")
    if all(w in _FILLER for w in re.findall(r"[\w'-]+", q.lower())):
        q = ""
    return ParsedQuery(raw=raw, text=q, filters=f, notes=notes)


def keywords(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in re.findall(r"\w+", text.lower()):
        if w not in STOPWORDS and len(w) > 1:
            seen[w] = None
    return list(seen)


def now_ts() -> float:
    return time.time()
