"""Confirmation previews for tool-format calls, whose args live in kwargs."""

from gptme.hooks.confirm import ConfirmAction
from gptme.tools.base import ToolUse


def test_preview_content_prefers_content():
    assert ToolUse("shell", [], "ls").preview_content == "ls"


def test_preview_content_from_tool_format_kwargs():
    ipython = ToolUse("ipython", None, None, kwargs={"code": "print(1)"})
    assert ipython.preview_content == "print(1)"
    shell = ToolUse("shell", None, None, kwargs={"command": "ls -la"})
    assert shell.preview_content == "ls -la"
    other = ToolUse("save", None, None, kwargs={"path": "a.py"})
    assert (
        other.preview_content is not None and '"path": "a.py"' in other.preview_content
    )
    assert ToolUse("x", None, None).preview_content is None


def test_guardrail_sees_tool_format_shell_command(monkeypatch):
    """A tool-format shell call without a preview is still checked."""
    from gptme.hooks import guardrails

    monkeypatch.setattr(guardrails, "_get_mode", lambda: "enforce")
    tool_use = ToolUse("shell", None, None, kwargs={"command": "rm -rf /"})
    result = guardrails.guardrail_hook(tool_use)
    assert result is not None and result.action == ConfirmAction.SKIP


def test_preview_content_shows_what_runs():
    """tmux runs ``command``: preview it even if ``code`` is also given."""
    both = ToolUse("tmux", None, None, kwargs={"code": "x", "command": "list-sessions"})
    assert both.preview_content == "list-sessions"


def test_preview_content_ignores_non_string_values():
    tu = ToolUse("tmux", None, None, kwargs={"code": 1, "command": "list-sessions"})  # type: ignore[dict-item]
    assert tu.preview_content == "list-sessions"
    only_ints = ToolUse("x", None, None, kwargs={"code": 1})  # type: ignore[dict-item]
    assert only_ints.preview_content == '{\n  "code": 1\n}'
