"""Credential-output policy must run before autonomous confirmation.

All execution callbacks below emit a synthetic marker only. Commands are inert
input to the confirmation chain: no environment dumps, credential reads, or
xtrace processes are ever launched, including when these regressions fail.
"""

from collections.abc import Generator
from pathlib import Path

import pytest

from gptme.hooks import HookType, get_hooks
from gptme.hooks.auto_confirm import register as register_auto_confirm
from gptme.hooks.guardrails import register as register_guardrails
from gptme.hooks.registry import HookRegistry, get_registry, set_registry
from gptme.message import Message
from gptme.tools.base import ToolUse, using_current_tool_use
from gptme.tools.shell import get_shell_command
from gptme.util.ask_execute import execute_with_confirmation

SYNTHETIC_MARKER = "GPTME_SYNTHETIC_CREDENTIAL_OUTPUT_EXECUTED"
COMMANDS = ["printenv", "bash -x -c 'printf synthetic-only'"]


@pytest.fixture(autouse=True)
def _isolated_confirmation_hooks() -> Generator[None, None, None]:
    """Avoid inherited approval hooks and restore the exact original registry."""
    original = get_registry()
    set_registry(HookRegistry())
    try:
        yield
    finally:
        set_registry(original)


def _execute_synthetic(
    command: str, shape: str, *, preview_only: bool = False
) -> tuple[list[Message], list[str]]:
    """Run production confirmation, never the real shell executor.

    Native calls carry the command in kwargs; Markdown calls carry content and
    a fenced preview. The preview-only case models an innocuous payload with a
    larger command sequence shown for approval.
    """
    payload = "printf synthetic-only" if preview_only else command
    tool_use = ToolUse(
        tool="shell",
        args=[],
        content=payload if shape == "markdown" else None,
        kwargs={"command": payload} if shape == "native" else {},
    )
    code = get_shell_command(tool_use.content, tool_use.args, tool_use.kwargs)
    received: list[str] = []

    def execute_fn(content: str, path: Path | None) -> Generator[Message, None, None]:
        received.append(content)
        yield Message("system", SYNTHETIC_MARKER)

    def preview_fn(content: str, path: Path | None) -> str:
        preview = f"{command}\n{content}" if preview_only else content
        return f"```bash\n{preview}\n```" if shape == "markdown" else preview

    with using_current_tool_use(tool_use):
        messages = list(
            execute_with_confirmation(
                code,
                args=tool_use.args,
                kwargs=tool_use.kwargs,
                execute_fn=execute_fn,
                get_path_fn=lambda code, args, kwargs: None,
                preview_fn=preview_fn,
            )
        )
    return messages, received


def _register_chain(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("GPTME_GUARDRAILS", mode)
    register_guardrails()
    register_auto_confirm()
    hooks = get_hooks(HookType.TOOL_CONFIRM)
    assert any(h.name == "auto_confirm" and h.enabled for h in hooks)
    if mode != "off":
        assert [h.name for h in hooks] == ["guardrails", "auto_confirm"]


@pytest.mark.parametrize("command", COMMANDS, ids=["printenv", "bash-xtrace"])
@pytest.mark.parametrize("shape", ["native", "markdown"])
def test_enforce_blocks_credential_output_before_auto_confirm(
    monkeypatch: pytest.MonkeyPatch, command: str, shape: str
) -> None:
    _register_chain(monkeypatch, "enforce")

    messages, received = _execute_synthetic(command, shape)

    assert received == [], "Credential-output commands must not reach the executor"
    assert all(SYNTHETIC_MARKER not in message.content for message in messages)
    assert any(
        message.role == "system" and "Blocked by guardrail" in message.content
        for message in messages
    ), [message.content for message in messages]


@pytest.mark.parametrize("command", COMMANDS, ids=["printenv", "bash-xtrace"])
@pytest.mark.parametrize("shape", ["native", "markdown"])
def test_enforce_checks_full_preview_not_only_safe_payload(
    monkeypatch: pytest.MonkeyPatch, command: str, shape: str
) -> None:
    _register_chain(monkeypatch, "enforce")

    messages, received = _execute_synthetic(command, shape, preview_only=True)

    assert received == [], (
        "Blocked commands in the approval preview must veto execution"
    )
    assert all(SYNTHETIC_MARKER not in message.content for message in messages)
    assert any("Blocked by guardrail" in message.content for message in messages)


@pytest.mark.parametrize("mode", ["off", "shadow"])
@pytest.mark.parametrize("command", COMMANDS, ids=["printenv", "bash-xtrace"])
@pytest.mark.parametrize("shape", ["native", "markdown"])
def test_non_enforcing_modes_retain_auto_confirm_behavior(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    command: str,
    shape: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _register_chain(monkeypatch, mode)

    messages, received = _execute_synthetic(command, shape)

    assert received == [command]
    assert [message.content for message in messages] == [SYNTHETIC_MARKER]
    if mode == "shadow":
        assert "Guardrail (shadow): would block" in caplog.text
    else:
        assert "Guardrail (shadow): would block" not in caplog.text


@pytest.mark.parametrize("shape", ["native", "markdown"])
@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        ("bash -s arg <<'EOF'\nprintenv\nEOF", True),
        ("bash - <<'EOF'\nprintenv\nEOF", True),
        ('echo "$(printf %s "$API_KEY")"', True),
        ("API_KEY=synthetic echo ready", False),
        ('echo "$(test -n "$API_KEY" && printf configured)"', False),
    ],
)
def test_review_regressions_through_confirmation_chain(
    monkeypatch: pytest.MonkeyPatch, command: str, blocked: bool, shape: str
) -> None:
    _register_chain(monkeypatch, "enforce")

    messages, received = _execute_synthetic(command, shape)

    if blocked:
        assert received == []
        assert any("Blocked by guardrail" in message.content for message in messages)
    else:
        assert received == [command]
        assert [message.content for message in messages] == [SYNTHETIC_MARKER]


@pytest.mark.parametrize("shape", ["native", "markdown"])
@pytest.mark.parametrize("mode", ["enforce", "shadow", "off"])
@pytest.mark.parametrize(
    "command",
    [
        "cat config/*",
        "grep -rn TOKEN .",
        "grep -r --binary-files text TOKEN .",
        "rg -S synthetic config/*",
        "grep -rn 'def foo' .",
    ],
)
def test_secret_reach_through_confirmation_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, mode: str, command: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TOKEN=synthetic-only\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "client.pem").write_text("SYNTHETIC-ONLY\n")
    _register_chain(monkeypatch, mode)

    messages, received = _execute_synthetic(command, shape)
    blocked = mode == "enforce" and "def foo" not in command
    assert received == ([] if blocked else [command])
    if blocked:
        assert any(
            "Secret path access blocked" in message.content for message in messages
        )
    else:
        assert [message.content for message in messages] == [SYNTHETIC_MARKER]
