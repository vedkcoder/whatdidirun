"""Mask secrets in commands before they are stored or embedded."""

from __future__ import annotations

import re

MASK = "<REDACTED>"

_SECRET_NAME = r"[A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|PASSPHRASE|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIALS?|AUTH)[A-Za-z0-9_]*"

# (pattern, replacement). Group 1 is kept as a prefix where present.
_RULES: list[tuple[re.Pattern[str], str]] = [
    # export FOO_TOKEN=xyz / FOO_PASSWORD="xyz" cmd
    (re.compile(rf"(\b{_SECRET_NAME}=)(\"[^\"]*\"|'[^']*'|\S+)", re.I), rf"\1{MASK}"),
    # --password xyz / --token=xyz / --api-key xyz
    (re.compile(r"(--?(?:password|passwd|pass|token|secret|api[-_]?key|access[-_]?key|auth[-_]?token|access[-_]?token|client[-_]?secret)(?:=|\s+))(\"[^\"]*\"|'[^']*'|\S+)", re.I), rf"\1{MASK}"),
    # mysql/mariadb -pSECRET
    (re.compile(r"(\b(?:mysql|mariadb|mysqldump|mysqladmin)\b[^|;&]*?\s-p)(?!\s)(\S+)"), rf"\1{MASK}"),
    # HTTP auth headers
    (re.compile(r"((?:Authorization|Proxy-Authorization)\s*:\s*(?:Bearer|Basic|Token|Bot)?\s*)[^'\"\s]+", re.I), rf"\1{MASK}"),
    (re.compile(r"((?:X-Api-Key|X-Auth-Token|Api-Key)\s*:\s*)[^'\"\s]+", re.I), rf"\1{MASK}"),
    # curl -u user:pass
    (re.compile(r"((?:-u|--user)\s+[^:\s]+:)(\S+)"), rf"\1{MASK}"),
    # scheme://user:pass@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)([^@/\s]+)(@)", re.I), rf"\1{MASK}\3"),
    # Well-known token shapes anywhere in the line
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"), MASK),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b"), MASK),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), MASK),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), MASK),  # JWT
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), MASK),
]


def redact(cmd: str) -> str:
    for pattern, repl in _RULES:
        cmd = pattern.sub(repl, cmd)
    return cmd
