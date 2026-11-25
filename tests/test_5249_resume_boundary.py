"""Regression coverage for resumed tool boundaries and rejected launch ownership."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("mode", ["thread", "subprocess"])
@pytest.mark.parametrize("saved", [None, ["shell", "ipython"]])
def test_continue_applies_current_restriction(tmp_path, monkeypatch, mode, saved):
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import Subagent, _subagents, _subagents_lock

    child = Subagent(
        "tighten",
        "task",
        None,
        tmp_path,
        None,
        execution_mode=mode,
        parent_tool_allowlist=saved,
    )
    monkeypatch.setattr(api, "_find_subagent", lambda *a, **kw: child)
    monkeypatch.setattr(Subagent, "continuation_blocker", lambda *a, **kw: None)
    monkeypatch.setattr(api, "get_session_allowlist", lambda: ["ipython"])
    captured = []

    def launch(**kwargs):
        captured.append(kwargs["parent_tool_allowlist"])
        return MagicMock()

    monkeypatch.setattr(api._exec, "_create_subagent_thread", launch)
    monkeypatch.setattr(api._exec, "_run_subagent_subprocess", launch)
    monkeypatch.setattr(api._exec, "_monitor_subprocess", lambda *a: None)
    with _subagents_lock:
        _subagents.append(child)
    try:
        api.subagent_continue("tighten", "continue")
        continued = next(s for s in reversed(_subagents) if s.agent_id == "tighten")
        assert continued.thread is not None
        continued.thread.join(timeout=10)
        assert not continued.thread.is_alive()
        assert captured == [["ipython"]]
        assert continued.parent_tool_allowlist == ["ipython"]
    finally:
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "tighten"]


def test_rejected_continue_never_acquires_writer(tmp_path, monkeypatch):
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import Subagent

    child = Subagent(
        "reject", "task", None, tmp_path, None, execution_mode="subprocess"
    )
    monkeypatch.setattr(api, "_find_subagent", lambda *a, **kw: child)
    monkeypatch.setattr(Subagent, "continuation_blocker", lambda *a, **kw: None)
    lease = MagicMock()
    monkeypatch.setattr(api._exec, "acquire_writer_lease", lease)

    def reject(*args):
        raise ValueError("tool selection rejected")

    monkeypatch.setattr(api._exec, "_capture_subprocess_tools", reject)
    with pytest.raises(ValueError, match="tool selection rejected"):
        api.subagent_continue("reject", "continue")
    lease.assert_not_called()


@pytest.mark.parametrize("planner", [False, True])
def test_rejected_spawn_never_allocates_isolation(tmp_path, monkeypatch, planner):
    import gptme.cli.main as cli
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import _subagents, _subagents_lock

    monkeypatch.setattr(cli, "get_logdir", lambda name: tmp_path / name)
    allocate = MagicMock(return_value=tmp_path / "isolated")
    monkeypatch.setattr("gptme.util.git_worktree.get_git_root", lambda path: tmp_path)
    monkeypatch.setattr("gptme.util.git_worktree.create_worktree", allocate)

    def reject(*args):
        raise ValueError("tool selection rejected")

    monkeypatch.setattr(api._exec, "_capture_subprocess_tools", reject)
    try:
        with pytest.raises(ValueError, match="tool selection rejected"):
            api.subagent(
                "reject-spawn",
                "task",
                use_subprocess=True,
                isolated=True,
                workdir=tmp_path,
                mode="planner" if planner else "executor",
                subtasks=[{"id": "check", "description": "verify", "role": "verify"}]
                if planner
                else None,
            )
        allocate.assert_not_called()
    finally:
        with _subagents_lock:
            _subagents[:] = [
                s for s in _subagents if not s.agent_id.startswith("reject-spawn")
            ]


def test_restricted_acp_rejected_before_allocation(tmp_path, monkeypatch):
    from gptme.tools.subagent import api

    monkeypatch.setattr(api, "get_session_allowlist", lambda: ["ipython"])
    allocate = MagicMock()
    monkeypatch.setattr("gptme.util.git_worktree.create_worktree", allocate)
    with pytest.raises(ValueError, match="ACP.*tool"):
        api.subagent(
            "restricted-acp", "task", use_acp=True, isolated=True, workdir=tmp_path
        )
    allocate.assert_not_called()


@pytest.mark.parametrize(
    ("saved", "current", "expected"),
    [
        (None, ["ipython"], ["ipython"]),
        (["ipython"], None, ["ipython"]),
        (["shell"], ["ipython"], []),
        (["*"], ["ipython"], ["ipython"]),
        (["shell", "ipython"], [], []),
    ],
)
def test_clarification_intersects_both_boundaries(
    tmp_path, monkeypatch, saved, current, expected
):
    from gptme.tools import get_session_allowlist, set_session_allowlist
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import ReturnType, Subagent

    child = Subagent(
        "reply-tighten", "task", None, tmp_path, None, parent_tool_allowlist=saved
    )
    monkeypatch.setattr(api, "_find_subagent", lambda *a, **kw: child)
    monkeypatch.setattr(
        Subagent, "status", lambda self: ReturnType("clarification_needed", "which?")
    )
    seen = []
    monkeypatch.setattr(
        api, "subagent", lambda **kw: seen.append(get_session_allowlist())
    )
    before = get_session_allowlist()
    set_session_allowlist(current)
    try:
        api.subagent_reply("reply-tighten", "this")
        assert seen == [expected]
        assert get_session_allowlist() == current
    finally:
        set_session_allowlist(before)


def test_intersection_preserves_hint_and_file_boundaries(tmp_path, monkeypatch):
    from gptme.tools.base import ToolSpec
    from gptme.tools.subagent import execution

    approved = tmp_path / "approved.py"
    approved.write_text(
        'from gptme.tools.base import ToolSpec\ntool = ToolSpec(name="custom_read", desc="read", hints=frozenset({"read-only"}))\n'
    )
    read = ToolSpec(name="project.read", desc="read", hints=frozenset({"read-only"}))
    write = ToolSpec(name="project.write", desc="write")
    monkeypatch.setattr(execution, "get_available_tools", lambda: [read, write])
    assert execution._intersect_tool_allowlists(["project.*"], ["hint:read-only"]) == [
        "project.read"
    ]
    assert execution._intersect_tool_allowlists(
        [str(approved)], ["hint:read-only"]
    ) == [str(approved)]
    assert execution._intersect_tool_allowlists(
        ["hint:read-only"], [str(approved)]
    ) == [str(approved)]
    approved.write_text(
        'from gptme.tools.base import ToolSpec\nread = ToolSpec(name="custom_read", desc="read")\nwrite = ToolSpec(name="custom_write", desc="write")\n'
    )
    with pytest.raises(ValueError, match="only part of tool file"):
        execution._intersect_tool_allowlists([str(approved)], ["custom_read"])


def test_empty_intersection_loads_no_default_tools():
    from gptme.tools import clear_tools, get_tools, init_tools
    from gptme.tools.subagent.execution import _intersect_tool_allowlists

    boundary = _intersect_tool_allowlists(["shell"], ["ipython"])
    assert boundary == []
    clear_tools()
    init_tools(boundary, include_mcp=False)
    assert get_tools() == []


def test_acp_continuation_obeys_new_limit(tmp_path, monkeypatch):
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import Subagent

    child = Subagent(
        "acp-old",
        "task",
        None,
        tmp_path,
        None,
        use_acp=True,
        execution_mode="acp",
        acp_session_id="session",
    )
    monkeypatch.setattr(api, "_find_subagent", lambda *a, **kw: child)
    monkeypatch.setattr(Subagent, "continuation_blocker", lambda *a, **kw: None)
    monkeypatch.setattr(api, "get_session_allowlist", lambda: ["ipython"])
    with pytest.raises(ValueError, match="ACP.*tool"):
        api.subagent_continue("acp-old", "continue")


def test_intersection_rejects_ambiguous_file_exports(tmp_path):
    from gptme.tools.subagent.execution import _intersect_tool_allowlists

    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    source = 'from gptme.tools.base import ToolSpec\ntool = ToolSpec(name="same_name", desc="tool")\n'
    first.write_text(source)
    second.write_text(source)
    with pytest.raises(ValueError, match="export.*same_name"):
        _intersect_tool_allowlists([str(first)], [str(second)])
