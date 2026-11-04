"""Confirmation metadata must reach approval surfaces without becoming editable."""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock

import pytest

from gptme.hooks import HookType, register_hook
from gptme.hooks.confirm import (
    ConfirmAction,
    ConfirmationResult,
    get_confirmation,
    preconfirmed,
    reset_auto_confirm,
)
from gptme.hooks.registry import HookRegistry, get_registry, set_registry
from gptme.message import Message
from gptme.tools.base import ToolUse, using_current_tool_use
from gptme.util.ask_execute import execute_with_confirmation

HEADER = "Memory scope: user\nDestination: /home/user/memory/preference.md"
CONTENT = "Prefer concise answers."


@pytest.fixture(autouse=True)
def isolated_registry() -> Generator[None, None, None]:
    previous = get_registry()
    set_registry(HookRegistry())
    reset_auto_confirm()
    try:
        yield
    finally:
        reset_auto_confirm()
        set_registry(previous)


def test_header_delivered_to_three_argument_hook_without_mutating_tool() -> None:
    tool = ToolUse("memory", [], CONTENT)
    seen = []

    def hook(
        tool_use: ToolUse, preview: str | None = None, workspace: Path | None = None
    ) -> ConfirmationResult:
        seen.append((tool_use, preview, workspace))
        return ConfirmationResult.confirm()

    register_hook("capture", HookType.TOOL_CONFIRM, hook)
    get_confirmation(tool, preview=CONTENT, preview_header=HEADER)
    delivered, preview, workspace = seen[0]
    assert delivered.confirmation_header == HEADER
    assert delivered is not tool
    assert tool.confirmation_header is None
    assert preview == CONTENT
    assert workspace is None
    with preconfirmed(tool):
        get_confirmation(tool, preview=CONTENT, preview_header=HEADER)
    assert len(seen) == 1


def test_helper_preserves_header_on_original_and_edited_confirmation() -> None:
    seen = []
    executed = []

    def hook(
        tool_use: ToolUse, preview: str | None = None, workspace: Path | None = None
    ) -> ConfirmationResult:
        seen.append((tool_use.confirmation_header, preview))
        return (
            ConfirmationResult.edit("Edited preference")
            if len(seen) == 1
            else ConfirmationResult.confirm()
        )

    def execute(content: str, path: Path | None) -> Generator[Message, None, None]:
        executed.append(content)
        yield Message("system", "done")

    register_hook("capture", HookType.TOOL_CONFIRM, hook)
    with using_current_tool_use(ToolUse("memory", [], CONTENT)):
        list(
            execute_with_confirmation(
                CONTENT,
                [],
                None,
                execute_fn=execute,
                get_path_fn=lambda *_: None,
                preview_header=HEADER,
            )
        )
    assert seen == [(HEADER, CONTENT), (HEADER, "Edited preference")]
    assert executed == ["Edited preference"]


def test_cli_header_outside_preview_and_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    from gptme.hooks import cli_confirm

    preview = Mock()
    editor = Mock(return_value="Edited preference")
    monkeypatch.setattr(cli_confirm, "print_preview", preview)
    monkeypatch.setattr(cli_confirm, "edit_text_with_editor", editor)
    monkeypatch.setattr(cli_confirm, "prompt_alert", lambda _: "e")
    monkeypatch.setattr(cli_confirm, "print_bell", lambda: None)
    monkeypatch.setattr(cli_confirm, "flush_stdin", lambda: None)
    cli_confirm.register()
    result = get_confirmation(
        ToolUse("memory", [], CONTENT), preview=CONTENT, preview_header=HEADER
    )
    assert result.action == ConfirmAction.EDIT
    preview.assert_called_once_with(CONTENT, "text", copy=True, header=HEADER)
    editor.assert_called_once_with(CONTENT, ext=None)


def test_server_pending_and_event_header_content_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gptme.hooks import server_confirm
    from gptme.server.api_v2_sessions import SessionManager

    events = []
    pending_records = []

    def add_event(conversation_id: str, event: dict) -> None:
        pending = server_confirm.get_pending(event["tool_id"])
        assert pending is not None
        events.append(event)
        pending_records.append(pending)
        server_confirm.resolve_pending(event["tool_id"], ConfirmationResult.confirm())

    monkeypatch.setattr(SessionManager, "add_event", add_event)
    token1 = server_confirm.current_conversation_id.set("test-conversation")
    token2 = server_confirm.current_session_id.set("test-session")
    server_confirm.register()
    try:
        result = get_confirmation(
            ToolUse("memory", [], "native content"),
            preview=CONTENT,
            preview_header=HEADER,
        )
    finally:
        server_confirm.current_conversation_id.reset(token1)
        server_confirm.current_session_id.reset(token2)
    assert result.action == ConfirmAction.CONFIRM
    assert events[0]["tooluse"]["confirmation_header"] == HEADER
    assert pending_records[0].tool_use.confirmation_header == HEADER
    assert events[0]["tooluse"]["content"] == pending_records[0].preview == CONTENT
    assert server_confirm.get_pending(events[0]["tool_id"]) is None


def test_print_preview_renders_header_literally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from gptme.util.ask_execute import print_preview

    header = "Scope: user /memory/[red]literal[/bold].md"
    print_preview(CONTENT, "text", header=header)
    assert header in capsys.readouterr().out
