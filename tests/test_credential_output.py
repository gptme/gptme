"""Credential-output policy tests use inert source strings, never real dumps."""

import pytest

from gptme.hooks.guardrails import guardrail_hook
from gptme.tools.base import ToolUse


@pytest.mark.parametrize(
    "command",
    [
        "bash -x script.sh",
        "bash -eux script.sh",
        "/bin/sh -x script.sh",
        "bash -o xtrace script.sh",
        "set -x; :",
        "set -eux",
        "set -o xtrace",
        "BASH_XTRACEFD=2 bash script.sh",
        "export BASH_XTRACEFD=2",
        "env BASH_XTRACEFD=2 bash script.sh",
        "printenv",
        "printenv OPENAI_API_KEY",
        "printenv SESSION_LABEL OPENAI_API_KEY",
        "printenv $(compgen -e)",
        'printenv "$NAME"',
        "printenv 'GITHUB_TOKEN'",
        "printenv -- -API_KEY SESSION_LABEL",
        "printenv --unexpected SESSION_LABEL",
        "printenv -$OPTIONS SESSION_LABEL",
        'printenv "-$OPTIONS" SESSION_LABEL',
        "printenv -- SESSION_LABEL -API_KEY",
        "printenv -0",
        "printenv --null",
        "printenv --",
        "env | grep '^SESSION_LABEL='",
        "env | grep -E '^(SESSION_LABEL|CARGO_TARGET_DIR)='",
        "env | grep '^PUBLIC_'",
        "printenv | grep '^SESSION_LABEL='",
        "env",
        "env -0",
        "/usr/bin/env --null",
        "set",
        "export -p",
        "declare -p",
        "typeset -p",
        "export",
        "declare",
        "typeset",
        "bash --rcfile harmless.rc -x script.sh",
        "bash -O extglob -x script.sh",
        "echo $API_KEY",
        'printf "%s" "$SUPABASE_MANAGEMENT_TOKEN"',
        "bash -c 'printenv'",
        "bash -xc 'echo synthetic'",
        "echo $(printenv)",
        "sudo bash -x script.sh",
        "command printenv",
        "env A=b printenv",
        "LOCAL_FLAG=1 printenv",
        "LOCAL_FLAG=1 bash -x script.sh",
        "env -- LOCAL_FLAG=1 printenv",
        "env -uMISSING printenv",
        "env -C/tmp printenv",
        "bash -c -x 'printf synthetic'",
        'command echo "$API_KEY"',
        'env printf "%s" "$API_KEY"',
        "env bash <<'EOF'\nprintenv\nEOF",
        "bash <<'EOF'\nset -x\nEOF",
        "bash -s arg <<'EOF'\nprintenv\nEOF",
        "bash - <<'EOF'\nprintenv\nEOF",
        "bash -s -- arg <<'EOF'\nprintenv\nEOF",
        "bash -es arg <<'EOF'\nprintenv\nEOF",
        "bash -s - arg <<'EOF'\nprintenv\nEOF",
        'echo "$(printf %s "$API_KEY")"',
        'API_KEY=synthetic echo "$OTHER_TOKEN"',
    ],
)
def test_credential_output_is_blocked(command, monkeypatch):
    monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
    result = guardrail_hook(ToolUse(tool="shell", args=[], content=command))
    assert result is not None, command
    assert "Credential output blocked" in (result.message or "")


@pytest.mark.parametrize(
    "command",
    [
        "env NAME=value python3 script.py",
        "env -i PATH=/usr/bin python3 script.py",
        "env -u TOKEN python3 script.py",
        "env --unset=TOKEN python3 script.py",
        "printenv PATH",
        "printenv HOME LANG",
        # Named non-secret operands match the echo "$NAME" rule.
        "printenv SESSION_LABEL CARGO_TARGET_DIR",
        "printenv -0 SESSION_LABEL",
        "printenv --null SESSION_LABEL",
        "printenv -- SESSION_LABEL CARGO_TARGET_DIR",
        "/usr/bin/printenv SESSION_LABEL",
        "command printenv SESSION_LABEL",
        "env -u TOKEN printenv SESSION_LABEL",
        "printenv --help",
        "printenv --version",
        'echo "${#API_KEY}"',
        "bash -c 'cat' <<'EOF'\nprintenv\nEOF",
        "set +x",
        "set +o xtrace",
        "set -- -x",
        "bash script.sh -x",
        "bash -c 'echo safe' ignored -x",
        "bash -- script.sh -x",
        "echo 'bash -x script.sh'",
        'printf "%s" "printenv"',
        "grep 'set -x' script.sh",
        "git commit -m 'use bash -x for debugging'",
        "myprintenv",
        "./script.sh -x",
        "cat <<'EOF'\nset -x\nprintenv\nEOF",
        "echo $HOME",
        'echo "$MONKEY"',
        'echo "${TOKEN:+set}"',
        'printf "%s" "${API_KEY:+present}"',
        "python3 script.py --api-key $API_KEY",
        "compgen -e",
        "declare -F",
        "set -e",
        "set -o",
        "set -o pipefail",
        "API_KEY=synthetic echo ready",
        "API_KEY=synthetic printf '%s' ready",
        'echo "$(test -n "$API_KEY" && printf configured)"',
        'printf "%s" "$(test -n "$API_KEY" && printf configured)"',
        "bash script.sh <<'EOF'\nprintenv\nEOF",
        "bash - script.sh <<'EOF'\nprintenv\nEOF",
        "bash -sc 'cat' arg <<'EOF'\nprintenv\nEOF",
        "bash -s arg <<'EOF'\nprintf configured\nEOF",
    ],
)
def test_benign_commands_are_not_credential_output(command, monkeypatch):
    monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
    assert guardrail_hook(ToolUse(tool="shell", args=[], content=command)) is None


def test_no_sensitive_value_in_reason_or_shadow_log(monkeypatch, caplog):
    command = "BASH_XTRACEFD=987654321 bash -x script.sh"
    monkeypatch.setenv("GPTME_GUARDRAILS", "enforce")
    result = guardrail_hook(ToolUse(tool="shell", args=[], content=command))
    assert result is not None
    assert "987654321" not in (result.message or "")
    monkeypatch.setenv("GPTME_GUARDRAILS", "shadow")
    assert guardrail_hook(ToolUse(tool="shell", args=[], content=command)) is None
    assert "Credential output blocked" in caplog.text
    assert "987654321" not in caplog.text
