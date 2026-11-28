"""Relaunch validation must not execute files whose approval changed."""

import pytest


@pytest.mark.parametrize("current_kind", ["names", "hint", "other_file"])
def test_changed_file_boundary_is_rejected_without_import(tmp_path, current_kind):
    from gptme.tools.subagent.execution import _intersect_tool_allowlists

    marker = tmp_path / "executed"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "from gptme.tools.base import ToolSpec\n"
        'tool = ToolSpec(name="custom_read", desc="read")\n'
    )
    revoked = tmp_path / "revoked.py"
    revoked.write_text(source)
    other = tmp_path / "other.py"
    other.write_text(source)
    current = {
        "names": ["ipython"],
        "hint": ["hint:read-only"],
        "other_file": [str(other)],
    }[current_kind]
    with pytest.raises(ValueError, match="tool.file.*boundary"):
        _intersect_tool_allowlists([str(revoked)], current)
    assert not marker.exists(), "relaunch validation imported an unapproved tool file"


def test_unchanged_file_boundary_needs_no_import(tmp_path):
    from gptme.tools.subagent.execution import _intersect_tool_allowlists

    missing = str(tmp_path / "removed.py")
    assert _intersect_tool_allowlists([missing], [missing]) == [missing]


@pytest.mark.parametrize("relaunch", ["continue", "reply"])
def test_relaunch_rejects_revoked_file_before_side_effects(
    tmp_path, monkeypatch, relaunch
):
    from unittest.mock import MagicMock

    from gptme.tools import get_session_allowlist, set_session_allowlist
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import ReturnType, Subagent

    marker = tmp_path / "executed"
    revoked = tmp_path / "revoked.py"
    revoked.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "from gptme.tools.base import ToolSpec\n"
        'tool = ToolSpec(name="custom_read", desc="read")\n'
    )
    child = Subagent(
        "revoked-child",
        "task",
        None,
        tmp_path / "child",
        None,
        execution_mode="subprocess",
        parent_tool_allowlist=[str(revoked)],
    )
    monkeypatch.setattr(api, "_find_subagent", lambda *a, **kw: child)
    monkeypatch.setattr(Subagent, "continuation_blocker", lambda *a, **kw: None)
    monkeypatch.setattr(
        Subagent,
        "status",
        lambda *a, **kw: ReturnType("clarification_needed", "which?"),
    )
    lease = MagicMock()
    spawn = MagicMock()
    monkeypatch.setattr(api._exec, "acquire_writer_lease", lease)
    monkeypatch.setattr(api, "subagent", spawn)
    previous = get_session_allowlist()
    set_session_allowlist(["ipython"])
    try:
        call = api.subagent_continue if relaunch == "continue" else api.subagent_reply
        with pytest.raises(ValueError, match="changed tool-file boundary"):
            call("revoked-child", "continue")
        assert not marker.exists()
        lease.assert_not_called()
        spawn.assert_not_called()
        assert get_session_allowlist() == ["ipython"]
    finally:
        set_session_allowlist(previous)
