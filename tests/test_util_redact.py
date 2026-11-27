"""Redaction of mid-line secret assignments and URL userinfo passwords.

Two common leak shapes were missed by the line-anchored patterns: a request
header copied out of a log (``-H "X-Api-Key: sk-..."``) or a JSON headers dict,
and a credential embedded in a URL (``https://user:password@host``).
"""

import pytest

from gptme.util.redact import (
    redact_secret_values_from_output,
    redact_secrets_from_text,
)

# Built at runtime so the literal never looks like a committed credential.
VENDOR_KEY = "sk-" + "proj-" + "AbCdEf0123456789abcdefXYZ"
OPAQUE = "Zq8" + "x7WmK2pL9vR4tY6uN3bH5jC1"
URL_PASSWORD = "hunter2" + "s3cr3t"


@pytest.mark.parametrize(
    "line",
    [
        f'-H "X-Api-Key: {VENDOR_KEY}"',
        f"X-Api-Key: {VENDOR_KEY}",
        f'headers={{"X-Api-Key": "{VENDOR_KEY}"}}',
        f"[debug] api_key: {VENDOR_KEY}",
        f"[debug] token={VENDOR_KEY}",
    ],
)
def test_midline_secret_assignment_is_redacted(line):
    out = redact_secrets_from_text(line)
    assert VENDOR_KEY not in out
    assert "[REDACTED]" in out
    # The surrounding context is preserved so the line stays readable.
    assert line.split(VENDOR_KEY)[0] in out


@pytest.mark.parametrize(
    "line",
    [
        f"curl https://user:{URL_PASSWORD}@example.com/api",
        f"DATABASE_URL: postgres://admin:{URL_PASSWORD}@db:5432/app",
        f"REDIS_URL=redis://:{URL_PASSWORD}@db:6379/0",
        f"git clone https://x-access-token:{URL_PASSWORD}@github.com/o/r",
        f"GET https://john@corp.com:{URL_PASSWORD}@example.com/v1",
        f"GET postgres://admin:{URL_PASSWORD}@tail@db/app",
    ],
)
def test_url_userinfo_password_is_redacted(line):
    out = redact_secrets_from_text(line)
    assert URL_PASSWORD not in out
    assert (
        "[REDACTED]@example.com" in out
        or "[REDACTED]@db" in out
        or "[REDACTED]@github.com" in out
    )


@pytest.mark.parametrize(
    "line",
    [
        'config = {"max_tokens": 1024}',
        "the token: rotate it monthly",
        "https://example.com:8080/path",
        "git@github.com:org/repo.git",
        "https://host:8443?u=a@b.com",
        "https://host:8443#u=a@b.com",
    ],
)
def test_non_secret_midline_shapes_are_kept(line):
    """A mid-line ``token:`` in code or prose must not corrupt the value."""
    assert redact_secrets_from_text(line) == line
    assert redact_secret_values_from_output(line) == (line, 0)


@pytest.mark.parametrize("value", [VENDOR_KEY, OPAQUE])
def test_output_redactor_catches_midline_header(value):
    out, n = redact_secret_values_from_output(f'curl -H "X-Api-Key: {value}"\n')
    assert n == 1
    assert value not in out


@pytest.mark.parametrize("suffix", ["", "&format=json"])
def test_midline_query_secret_is_redacted(suffix):
    line = f"curl https://example.com/v1?api_key={OPAQUE}{suffix}"
    expected = line.replace(OPAQUE, "[REDACTED]")
    assert redact_secrets_from_text(line) == expected
    assert redact_secret_values_from_output(line) == (expected, 1)


@pytest.mark.parametrize("username", ["user", "", "john@corp.com"])
@pytest.mark.parametrize("password_tail", ["", "@tail"])
def test_output_redactor_catches_url_password(username, password_tail):
    password = URL_PASSWORD + password_tail
    line = f"GET redis://{username}:{password}@example.com/0\n"
    out, n = redact_secret_values_from_output(line)
    assert n == 1
    assert out == line.replace(password, "[REDACTED]")


@pytest.mark.parametrize(
    "redactor", ["redact_secrets_from_text", "redact_secret_values_from_output"]
)
@pytest.mark.parametrize("scheme_run", ["a-", "a.", "a+"])
def test_url_scan_does_not_retry_overlapping_schemes(redactor, scheme_run):
    """Bound pathological URL scans without leaving a stuck pytest process."""
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"from gptme.util.redact import {redactor} as redact; "
                f"line = {scheme_run!r} * 100000 + ' :// @'; "
                "result = redact(line); "
                "assert (result[0] if isinstance(result, tuple) else result) == line"
            ),
        ],
        check=True,
        timeout=10,
    )


def test_output_redactor_keeps_source_code():
    line = 'API_KEY = os.environ["OPENAI_API_KEY"]\n'
    out, n = redact_secret_values_from_output(line)
    assert n == 0
    assert out == line
