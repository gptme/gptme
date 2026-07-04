"""Tests for computer_task() — the context-efficient subagent wrapper (issue #216).

computer_task() is the "context-efficient tool-use loop until goal is achieved"
the original issue asked for: it delegates a multi-step computer-use task to a
subagent so that intermediate screenshots don't pile up in the caller's context.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_status(status: str = "success", result: str = "Task done.") -> dict:
    return {"status": status, "result": result}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_computer_task_returns_status_and_result(monkeypatch):
    """computer_task() returns a dict with 'status', 'result', and 'agent_id'."""
    import gptme.tools.subagent as _sa_mod
    from gptme.tools.computer import computer_task

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        pass

    def fake_wait(agent_id, timeout=300):
        return _make_status("success", "Screenshot saved to /tmp/desktop.png")

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    result = computer_task("Take a screenshot and save it to /tmp/desktop.png")

    assert result["status"] == "success"
    assert "desktop.png" in result["result"]
    assert "agent_id" in result


def test_computer_task_uses_computer_use_profile(monkeypatch):
    """computer_task() always spawns the subagent with profile='computer-use'."""
    from gptme.tools.computer import computer_task

    captured: list[dict] = []

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        captured.append(
            {
                "profile": profile,
                "max_time": kw.get("max_time"),
                "timeout": kw.get("timeout"),
            }
        )

    def fake_wait(agent_id, timeout=300):
        return _make_status()

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    computer_task("do something")

    assert len(captured) == 1
    assert captured[0]["profile"] == "computer-use"
    assert captured[0]["timeout"] is None


def test_computer_task_passes_timeout(monkeypatch):
    """The timeout arg sets subagent max_time and the wait deadline."""
    from gptme.tools.computer import computer_task

    spawn_kwargs: dict = {}
    wait_timeout: list[int] = []

    def fake_subagent(*args, **kwargs):
        spawn_kwargs.update(kwargs)

    def fake_wait(agent_id, timeout=300):
        wait_timeout.append(timeout)
        return _make_status()

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    computer_task("tweet 'hello'", timeout=42)

    assert spawn_kwargs["max_time"] == 42
    assert "timeout" not in spawn_kwargs
    assert wait_timeout == [42]


def test_computer_task_passes_model_override(monkeypatch):
    """The model arg is forwarded to subagent()."""
    from gptme.tools.computer import computer_task

    captured_model: list[str | None] = []

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        captured_model.append(model)

    def fake_wait(agent_id, timeout=300):
        return _make_status()

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    computer_task("do something", model="claude-opus-4-8")

    assert captured_model == ["claude-opus-4-8"]


def test_computer_task_default_model_is_none(monkeypatch):
    """When model is not specified, None is passed to subagent() (inherit parent)."""
    from gptme.tools.computer import computer_task

    captured_model: list[str | None] = []

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        captured_model.append(model)

    def fake_wait(agent_id, timeout=300):
        return _make_status()

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    computer_task("do something")

    assert captured_model == [None]


def test_computer_task_agent_id_is_unique(monkeypatch):
    """Each call to computer_task() uses a fresh, unique agent_id."""
    from gptme.tools.computer import computer_task

    agent_ids: list[str] = []

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        agent_ids.append(agent_id)

    def fake_wait(agent_id, timeout=300):
        return _make_status()

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    computer_task("task A")
    computer_task("task B")

    assert len(agent_ids) == 2
    assert agent_ids[0] != agent_ids[1]
    # IDs have the expected prefix
    assert agent_ids[0].startswith("computer-task-")
    assert agent_ids[1].startswith("computer-task-")


def test_computer_task_result_carries_agent_id(monkeypatch):
    """result['agent_id'] lets callers call subagent_read_log() for full transcript."""
    from gptme.tools.computer import computer_task

    spawned_ids: list[str] = []

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        spawned_ids.append(agent_id)

    def fake_wait(agent_id, timeout=300):
        return _make_status("success", "Done")

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    result = computer_task("some task")

    assert result["agent_id"] == spawned_ids[0]


def test_computer_task_propagates_failure_status(monkeypatch):
    """If the subagent fails, the status is propagated unchanged."""
    from gptme.tools.computer import computer_task

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        pass

    def fake_wait(agent_id, timeout=300):
        return _make_status("failure", "Could not open Firefox — no display available.")

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    result = computer_task("tweet something")

    assert result["status"] == "failure"
    assert "display" in result["result"]


def test_computer_task_propagates_timeout_status(monkeypatch):
    """If the subagent times out, the timeout status is propagated unchanged."""
    from gptme.tools.computer import computer_task

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        pass

    def fake_wait(agent_id, timeout=300):
        return _make_status("timeout", "Auto-cancelled after 42s")

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    result = computer_task("tweet something", timeout=42)

    assert result["status"] == "timeout"
    assert "42s" in result["result"]


def test_computer_task_propagates_clarification_needed_status(monkeypatch):
    """If the subagent asks for clarification, the status is propagated unchanged."""
    from gptme.tools.computer import computer_task

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        pass

    def fake_wait(agent_id, timeout=300):
        return _make_status(
            "clarification_needed",
            "Which browser profile should I use?",
        )

    import gptme.tools.subagent as _sa_mod

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    result = computer_task("log into the staging dashboard")

    assert result["status"] == "clarification_needed"
    assert "browser profile" in result["result"]


def test_computer_task_registered_in_tool_spec():
    """computer_task is registered as a ToolFunction in the computer ToolSpec."""
    from gptme.tools.computer import tool

    fn_names = [f.name for f in tool.functions or []]
    assert "computer_task" in fn_names, (
        f"computer_task not found in tool.functions; found: {fn_names}"
    )


# ---------------------------------------------------------------------------
# Tests for logdir / conversation in result (audit trail discoverability)
# ---------------------------------------------------------------------------


def test_computer_task_result_includes_logdir_and_conversation(monkeypatch, tmp_path):
    """When the subagent has a logdir, result carries 'logdir' and 'conversation'."""
    from unittest.mock import MagicMock

    import gptme.tools.subagent as _sa_mod
    import gptme.tools.subagent.types as _sa_types
    from gptme.tools.computer import computer_task

    fake_logdir = tmp_path / "subagent-computer-task-deadbeef"
    fake_logdir.mkdir()

    fake_sa = MagicMock()
    fake_sa.agent_id = None  # will be set during the call
    fake_sa.logdir = fake_logdir

    spawned_ids: list[str] = []

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        spawned_ids.append(agent_id)
        fake_sa.agent_id = agent_id

    def fake_wait(agent_id, timeout=300):
        return _make_status("success", "Done.")

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)

    monkeypatch.setattr(_sa_types, "_subagents", [fake_sa])

    result = computer_task("take a screenshot")

    assert "logdir" in result, "result should include 'logdir'"
    assert "conversation" in result, "result should include 'conversation'"
    assert result["logdir"] == str(fake_logdir)
    assert result["conversation"] == fake_logdir.name


def test_computer_task_result_missing_logdir_does_not_crash(monkeypatch):
    """If the subagent is not in the registry (shouldn't happen), result is still returned."""
    import gptme.tools.subagent as _sa_mod
    import gptme.tools.subagent.types as _sa_types
    from gptme.tools.computer import computer_task

    def fake_subagent(agent_id, prompt, profile=None, model=None, **kw):
        pass

    def fake_wait(agent_id, timeout=300):
        return _make_status("success", "Done.")

    monkeypatch.setattr(_sa_mod, "subagent", fake_subagent)
    monkeypatch.setattr(_sa_mod, "subagent_wait", fake_wait)
    # Empty _subagents — subagent lookup fails gracefully
    monkeypatch.setattr(_sa_types, "_subagents", [])

    result = computer_task("take a screenshot")

    # Status and agent_id are always present
    assert result["status"] == "success"
    assert "agent_id" in result
    # logdir / conversation absent when lookup fails — that's fine
    assert result.get("logdir") is None
    assert result.get("conversation") is None
