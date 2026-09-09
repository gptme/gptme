"""Server skill ownership across generation, tools, and revoked epochs."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytest.importorskip("flask")

from gptme.config import ChatConfig
from gptme.lessons.index import LessonIndex, clear_cache
from gptme.lessons.skill_commands import (
    register_skill_commands,
    unregister_skill_commands,
)
from gptme.lessons.skill_events import (
    read_skill_events,
    record_skill_phase,
    start_skill_invocation,
)
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.server.session_models import SessionManager
from gptme.server.session_step import start_tool_execution, step


@pytest.fixture
def skill_conversation(client, tmp_path, monkeypatch):
    name = f"test-skill-{uuid4().hex}"
    result = client.put(
        f"/api/v2/conversations/{name}",
        json={
            "prompt": "Be helpful.",
            "config": {"chat": {"workspace": str(tmp_path)}},
        },
    )
    assert result.status_code == 200
    session = SessionManager.get_session(result.get_json()["session_id"])
    assert session is not None
    manager = LogManager.load(name, lock=False)
    invocation_id = start_skill_invocation(
        manager.logdir, "demo", tmp_path / "SKILL.md"
    )
    assert invocation_id
    record_skill_phase(manager.logdir, invocation_id, "queued")
    manager.append(
        Message(
            "user", "Run the skill", metadata={"skill_invocation_id": invocation_id}
        )
    )
    manager.write()
    monkeypatch.setattr("gptme.server.session_step.trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(
        "gptme.server.session_step._try_auto_name_and_notify", lambda *a, **kw: None
    )
    yield name, session, manager
    SessionManager.remove_session(session.id)


def run_step(conversation, monkeypatch, output="Done."):
    name, session, manager = conversation
    monkeypatch.setattr(
        "gptme.server.session_step._chat_complete", lambda *a, **kw: (output, None)
    )
    session.generating = True
    session.step_seq += 1
    step(
        name,
        session,
        "openai/gpt-4o-mini",
        manager.workspace,
        stream=False,
        step_seq=session.step_seq,
    )


def phases(manager):
    return [e.phase for e in read_skill_events(manager.logdir)]


def test_server_command_queue_is_not_completion(client, tmp_path, monkeypatch):
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\ndescription: demo\n---\nSay hello.")
    monkeypatch.setattr(
        LessonIndex, "_default_dirs", staticmethod(lambda: [skill.parent.parent])
    )
    clear_cache()
    register_skill_commands()
    name = f"test-skill-command-{uuid4().hex}"
    response = client.put(
        f"/api/v2/conversations/{name}", json={"prompt": "Be helpful."}
    )
    session = SessionManager.get_session(response.get_json()["session_id"])
    assert session
    try:
        response = client.post(
            f"/api/v2/conversations/{name}",
            json={"role": "user", "content": "/skill:demo"},
        )
        assert response.status_code == 200
        manager = LogManager.load(name, lock=False)
        assert phases(manager) == ["started", "queued"]
        monkeypatch.setattr(
            "gptme.server.session_step.trigger_hook", lambda *a, **kw: []
        )
        monkeypatch.setattr(
            "gptme.server.session_step._try_auto_name_and_notify", lambda *a, **kw: None
        )
        run_step((name, session, manager), monkeypatch)
        assert phases(manager) == ["started", "queued", "completed"]
        SessionManager.remove_session(session.id)
        assert phases(manager) == ["started", "queued", "completed"]
    finally:
        SessionManager.remove_session(session.id)
        unregister_skill_commands()
        clear_cache()


def test_server_tools_wait_for_final_response(skill_conversation, monkeypatch):
    name, session, manager = skill_conversation
    run_step(skill_conversation, monkeypatch, "```shell\nprintf hello\n```\n")
    assert phases(manager) == ["started", "queued"]
    assert session.pending_tools
    # Execute the real shell tool, but control when the continuation starts.
    monkeypatch.setattr(
        "gptme.server.session_step._start_step_thread", lambda *a, **kw: True
    )
    tool_id = next(iter(session.pending_tools))
    thread = start_tool_execution(
        name,
        session,
        tool_id,
        None,
        "openai/gpt-4o-mini",
        ChatConfig.load_or_create(manager.logdir, ChatConfig()),
    )
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert not session.pending_tools
    assert not session._executing_tools
    assert phases(manager) == ["started", "queued"]
    run_step(skill_conversation, monkeypatch)
    assert phases(manager) == ["started", "queued", "completed"]


def test_server_generation_error(skill_conversation, monkeypatch):
    name, session, manager = skill_conversation

    def fail(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("gptme.server.session_step._chat_complete", fail)
    session.generating = True
    step(name, session, "openai/gpt-4o-mini", manager.workspace, stream=False)
    assert phases(manager) == ["started", "queued", "failed"]
    assert read_skill_events(manager.logdir)[-1].error_type == "RuntimeError"


def test_server_interrupt_revokes_stale_worker(skill_conversation, client, monkeypatch):
    name, session, manager = skill_conversation
    old_seq = session.step_seq
    session.generating = True

    def interrupt_during_reply(*args, **kwargs):
        response = client.post(
            f"/api/v2/conversations/{name}/interrupt", json={"session_id": session.id}
        )
        assert response.status_code == 200
        # Simulate a replacement dispatch before the old worker finishes.
        session.generating = True
        session.interrupted = False
        session.step_seq += 1
        return "Old response", None

    monkeypatch.setattr(
        "gptme.server.session_step._chat_complete", interrupt_during_reply
    )
    step(
        name,
        session,
        "openai/gpt-4o-mini",
        manager.workspace,
        stream=False,
        step_seq=old_seq,
    )
    assert phases(manager) == ["started", "queued", "abandoned"]
    assert session.generating
    # Even a fresh replay cannot overwrite the terminal event.
    run_step(skill_conversation, monkeypatch)
    assert phases(manager) == ["started", "queued", "abandoned"]


@pytest.mark.parametrize("reason", ["skip", "remove", "expire"])
def test_server_abandons_owned_invocation(
    skill_conversation, client, monkeypatch, reason
):
    name, session, manager = skill_conversation
    unrelated = start_skill_invocation(
        manager.logdir, "other", manager.logdir / "OTHER.md", session_id="other-run"
    )
    run_step(skill_conversation, monkeypatch, "```shell\nprintf hello\n```\n")
    if reason == "skip":
        monkeypatch.setattr(
            "gptme.server.api_v2_sessions._start_step_thread", lambda *a, **kw: True
        )
        response = client.post(
            f"/api/v2/conversations/{name}/tool/confirm",
            json={
                "session_id": session.id,
                "tool_id": next(iter(session.pending_tools)),
                "action": "skip",
            },
        )
        assert response.status_code == 200
    elif reason == "remove":
        SessionManager.remove_session(session.id)
    else:
        session.last_activity = datetime.now(timezone.utc) - timedelta(hours=2)
        SessionManager.clean_inactive_sessions()
    events = read_skill_events(manager.logdir)
    assert [e.phase for e in events if e.invocation_id != unrelated] == [
        "started",
        "queued",
        "abandoned",
    ]
    assert [e.phase for e in events if e.invocation_id == unrelated] == ["started"]


def test_server_latest_user_turn_only(skill_conversation, monkeypatch):
    name, session, manager = skill_conversation
    manager.append(Message("assistant", "Previous response"))
    manager.append(Message("user", "Unrelated new prompt"))
    manager.write()
    run_step(skill_conversation, monkeypatch)
    assert phases(manager) == ["started", "queued"]
