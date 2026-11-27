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
        f"git clone https://x-access-token:{URL_PASSWORD}@github.com/o/r",
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
    ],
)
def test_non_secret_midline_shapes_are_kept(line):
    """A mid-line ``token:`` in code or prose must not corrupt the value."""
    assert redact_secrets_from_text(line) == line


def test_output_redactor_catches_midline_header():
    out, n = redact_secret_values_from_output(f'curl -H "X-Api-Key: {VENDOR_KEY}"\n')
    assert n == 1
    assert VENDOR_KEY not in out


def test_output_redactor_catches_url_password():
    out, n = redact_secret_values_from_output(
        f"GET https://user:{URL_PASSWORD}@example.com/v1\n"
    )
    assert n == 1
    assert URL_PASSWORD not in out


def test_output_redactor_keeps_source_code():
    line = 'API_KEY = os.environ["OPENAI_API_KEY"]\n'
    out, n = redact_secret_values_from_output(line)
    assert n == 0
    assert out == line
