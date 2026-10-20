"""Secret values printed by shell commands must not reach the conversation.

Regression for an accidental ``env`` dump: a script's comment line broke a
backslash-continued ``env -u ... cmd`` so ``env`` ran bare and printed every
credential in the environment into the tool result.
"""

import pytest

import gptme.tools.shell as shell_module
from gptme.tools.shell import execute_shell
from gptme.util.redact import redact_secret_values_from_output

# Built at runtime so the literal never looks like a committed credential.
GH_TOKEN = "gh" + "p_" + "A1b2C3d4" * 5
OR_KEY = "sk-" + "or-v1-" + "0123456789abcdef" * 4
OPAQUE = "Zq8" + "x7WmK2pL9vR4tY6uN3bH5jC1"


def test_env_dump_values_are_redacted():
    text = (
        f"GITHUB_TOKEN={GH_TOKEN}\n"
        f"OPENROUTER_API_KEY={OR_KEY}\n"
        f"export SERVICE_SECRET='{OPAQUE}'\n"
        "HOME=/home/bob\n"
    )
    out, n = redact_secret_values_from_output(text)
    assert n == 3
    assert GH_TOKEN not in out and OR_KEY not in out and OPAQUE not in out
    assert "GITHUB_TOKEN=[REDACTED]\n" in out
    assert "HOME=/home/bob\n" in out


def test_yaml_style_value_is_redacted():
    out, n = redact_secret_values_from_output(f"  github_token: {GH_TOKEN}\n")
    assert n == 1
    assert out == "  github_token: [REDACTED]\n"


@pytest.mark.parametrize(
    "line",
    [
        "TOKEN_LIMIT = 4096\n",
        'API_KEY = os.environ["OPENAI_API_KEY"]\n',
        'GITHUB_TOKEN="$(gh auth token)"\n',
        "password: ${DB_PASSWORD}\n",
        "max_tokens: 1024\n",
    ],
)
def test_source_code_lines_are_kept(line):
    """Shell output often holds source code the agent will edit next."""
    out, n = redact_secret_values_from_output(line)
    assert n == 0
    assert out == line


def _run(monkeypatch, command: str) -> str:
    monkeypatch.setattr(
        shell_module, "check_with_shellcheck", lambda cmd: (False, False, "")
    )
    messages = list(execute_shell(None, None, {"command": command}))
    return "\n".join(m.content for m in messages)


def test_shell_tool_redacts_env_dump(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", GH_TOKEN)
    monkeypatch.setenv("OPENROUTER_API_KEY", OR_KEY)
    result = _run(monkeypatch, "env | grep -E '^(GITHUB_TOKEN|OPENROUTER_API_KEY)='")

    assert GH_TOKEN not in result
    assert OR_KEY not in result
    assert "GITHUB_TOKEN=[REDACTED]" in result
    assert "Redacted secret values on 2 output lines" in result


def test_shell_tool_redacts_stderr(monkeypatch):
    monkeypatch.setenv("FAKE_VALUE", OR_KEY)
    result = _run(monkeypatch, 'echo "API_KEY=$FAKE_VALUE" >&2')
    assert OR_KEY not in result
    assert "API_KEY=[REDACTED]" in result


def test_shell_tool_redaction_opt_out(monkeypatch):
    monkeypatch.setenv("GPTME_SHELL_REDACT_SECRETS", "0")
    monkeypatch.setenv("FAKE_VALUE", GH_TOKEN)
    result = _run(monkeypatch, 'echo "GITHUB_TOKEN=$FAKE_VALUE"')
    assert GH_TOKEN in result
    assert "Redacted secret values" not in result
