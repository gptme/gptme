"""Tests for path_deny workspace file filtering."""

from pathlib import Path

from gptme.message import Message
from gptme.tools.subagent.context import apply_path_deny


def test_apply_path_deny_no_patterns():
    """No deny patterns should return messages unchanged."""
    msgs = [Message("system", "```hello.py\nprint('hi')\n```")]
    result = apply_path_deny(msgs, [], Path("/tmp"))
    assert result[0].content == msgs[0].content


def test_apply_path_deny_excludes_matching_file():
    """A denied file should be removed from the context."""
    msgs = [
        Message(
            "system",
            "## Workspace\n\n```secret.py\nPASSWORD=hunter2\n```\n\n```safe.py\nx = 1\n```",
        )
    ]
    result = apply_path_deny(msgs, ["*.secret", "secret.py"], Path("/tmp"))
    content = result[0].content
    assert "safe.py" in content
    assert "PASSWORD" not in content
    assert "hunter2" not in content


def test_apply_path_deny_excludes_by_filename():
    """Matching just the filename should work."""
    msgs = [Message("system", "```config/secrets.env\nKEY=val\n```")]
    result = apply_path_deny(msgs, ["*.env"], Path("/tmp"))
    assert "KEY=val" not in result[0].content


def test_apply_path_deny_excludes_by_workspace_relative_path():
    """When workspace is provided, relative paths should match."""
    msgs = [Message("system", "```/abs/path/to/secrets.yaml\nkey: val\n```")]
    # Workspace root is /abs/path/to, so secrets.yaml is relative
    result = apply_path_deny(msgs, ["secrets.yaml"], Path("/abs/path/to"))
    assert "key: val" not in result[0].content


def test_apply_path_deny_non_system_messages_unchanged():
    """Only system messages should be filtered."""
    msgs = [
        Message("user", "```secret.py\nPASSWORD=test\n```"),
    ]
    result = apply_path_deny(msgs, ["secret.py"], Path("/tmp"))
    assert "PASSWORD=test" in result[0].content


def test_apply_path_deny_none_path_deny():
    """None path_deny should be a no-op."""
    msgs = [Message("system", "```secret.py\nx=1\n```")]
    result = apply_path_deny(msgs, None, Path("/tmp"))  # type: ignore[arg-type]
    assert "x=1" in result[0].content
