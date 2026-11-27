import pytest

from recall.redact import MASK, redact


@pytest.mark.parametrize("cmd,secret", [
    ("export AWS_SECRET_ACCESS_KEY=abc123xyz", "abc123xyz"),
    ("GITHUB_TOKEN='ghp_xxx' gh pr list", "ghp_xxx"),
    ("mysql -u root -phunter2 db", "hunter2"),
    ("psql postgres://admin:s3cret@db:5432/app", "s3cret"),
    ("curl -H 'Authorization: Bearer abc.def.ghi' https://x", "abc.def.ghi"),
    ("curl -u bob:pa55 https://x", "pa55"),
    ("tool --password=letmein", "letmein"),
    ("tool --api-key ZZZZ1234", "ZZZZ1234"),
    ("echo AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ("git clone https://ghp_" + "a" * 36 + "@github.com/x/y", "ghp_" + "a" * 36),
    ("ANTHROPIC_API_KEY=sk-ant-api03-" + "b" * 30 + " run", "sk-ant"),
])
def test_secrets_masked(cmd, secret):
    out = redact(cmd)
    assert secret not in out
    assert MASK in out


@pytest.mark.parametrize("cmd", ["ls -la", "git commit -m 'fix token parsing'", "docker run -p 8080:80 nginx", "mysql -u root -p db"])
def test_harmless_unchanged(cmd):
    assert redact(cmd) == cmd
