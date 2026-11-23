"""Hook parity between the CLI loop and the server loop (gptme/gptme#5091).

``GENERATION_PRE`` fires inside ``llm.reply()``, which the server skips by
calling ``_chat_complete`` / ``_stream`` directly. These tests pin the server's
explicit triggers, and guard against the two loops drifting apart again.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("flask")

import gptme
from gptme.hooks import HookType, register_hook, unregister_hook
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.server.session_models import SessionManager
from gptme.server.session_step import step
from gptme.tools.complete import SessionCompleteException

MODEL = "openai/gpt-4o-mini"


@pytest.fixture
def conversation(client, tmp_path, monkeypatch):
    name = f"test-hook-parity-{uuid4().hex}"
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
    manager.append(Message("user", "Hello"))
    manager.write()

    # Only the GENERATION hooks under test run for real; other hook types are
    # covered by tests/test_server_v2_hooks.py.
    from gptme.hooks import trigger_hook as real_trigger_hook

    def selective_trigger(hook_type, *args, **kwargs):
        if hook_type in (HookType.GENERATION_PRE, HookType.GENERATION_POST):
            return real_trigger_hook(hook_type, *args, **kwargs)
        return []

    monkeypatch.setattr("gptme.server.session_step.trigger_hook", selective_trigger)
    monkeypatch.setattr(
        "gptme.server.session_step._try_auto_name_and_notify", lambda *a, **kw: None
    )
    yield name, session, manager
    SessionManager.remove_session(session.id)


def run_step(conversation) -> None:
    name, session, manager = conversation
    session.generating = True
    session.step_seq += 1
    step(
        name, session, MODEL, manager.workspace, stream=False, step_seq=session.step_seq
    )


def test_server_step_fires_generation_hooks(conversation, monkeypatch):
    name, session, manager = conversation
    sent: list[list[Message]] = []
    pre_calls: list[dict] = []
    post_calls: list[Message] = []

    def pre_hook(messages, **kwargs):
        pre_calls.append({"n": len(messages), **kwargs})
        yield Message("system", "GENERATION_PRE_CONTEXT")

    def post_hook(message, **kwargs):
        post_calls.append(message)
        return
        yield  # pragma: no cover - makes this a generator

    def complete(messages, *a, **kw):
        sent.append(list(messages))
        return "Hi there.", None

    monkeypatch.setattr("gptme.server.session_step._chat_complete", complete)
    register_hook("parity_pre", HookType.GENERATION_PRE, pre_hook)
    register_hook("parity_post", HookType.GENERATION_POST, post_hook)
    try:
        run_step(conversation)
    finally:
        unregister_hook("parity_pre", HookType.GENERATION_PRE)
        unregister_hook("parity_post", HookType.GENERATION_POST)

    # PRE ran once, saw the prepared messages, and got the CLI's kwargs.
    assert len(pre_calls) == 1
    assert pre_calls[0]["model"] == MODEL
    assert pre_calls[0]["workspace"] == manager.workspace
    # Its context reached the provider call for this request only...
    assert len(sent) == 1
    assert any(m.content == "GENERATION_PRE_CONTEXT" for m in sent[0])
    # ...and was not persisted to the conversation log.
    reloaded = LogManager.load(name, lock=False)
    assert all(m.content != "GENERATION_PRE_CONTEXT" for m in reloaded.log.messages)
    # POST saw the committed assistant reply.
    assert [m.content for m in post_calls] == ["Hi there."]
    assert post_calls[0].role == "assistant"


def test_generation_pre_session_complete_is_clean_stop(conversation, monkeypatch):
    """complete/clarify/yield stop the session from GENERATION_PRE."""
    name, session, manager = conversation
    called = []
    session.checkpoint_needs_continuation = True

    def stop_hook(messages, **kwargs):
        raise SessionCompleteException("stop")
        yield  # pragma: no cover - makes this a generator

    def complete(*a, **kw):
        called.append(True)
        return "should not be generated", None

    monkeypatch.setattr("gptme.server.session_step._chat_complete", complete)
    register_hook("parity_stop", HookType.GENERATION_PRE, stop_hook, priority=10_000)
    try:
        run_step(conversation)
    finally:
        unregister_hook("parity_stop", HookType.GENERATION_PRE)

    assert not called, "provider must not be called after a session-stop signal"
    assert not session.generating, "reservation must be released"
    assert session.last_error is None
    assert not session.checkpoint_needs_continuation
    reloaded = LogManager.load(name, lock=False)
    assert not any(
        m.role == "system" and (m.content or "").startswith("Error:")
        for m in reloaded.log.messages
    ), "a session-stop signal is not a generation error"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("revoke", ["interrupt", "replace"])
def test_generation_pre_revocation_skips_provider(
    conversation, monkeypatch, stream, revoke
):
    _, session, manager = conversation
    called = []

    def revoke_hook(messages, **kwargs):
        with session.step_lock:
            if revoke == "interrupt":
                session.interrupted = True
            else:
                session.step_seq += 1
                session.generating = True
        yield Message("system", "Hook finished after revocation")

    def provider(*a, **kw):
        called.append(True)
        return "stale", None

    monkeypatch.setattr("gptme.server.session_step._chat_complete", provider)
    monkeypatch.setattr("gptme.server.session_step._stream", provider)
    register_hook(
        "parity_revoke", HookType.GENERATION_PRE, revoke_hook, priority=10_000
    )
    try:
        session.generating = True
        session.step_seq += 1
        step(
            conversation[0],
            session,
            MODEL,
            manager.workspace,
            stream=stream,
            step_seq=session.step_seq,
        )
    finally:
        unregister_hook("parity_revoke", HookType.GENERATION_PRE)

    assert not called, "revoked work must not start a provider request"
    assert session.last_error is None
    if revoke == "replace":
        assert session.generating, "stale cleanup must not release the replacement"


def test_server_generation_cannot_restart_process(conversation, monkeypatch):
    import gptme.tools.restart as restart

    _, session, manager = conversation
    manager.append(Message("assistant", "```restart\n\n```"))
    manager.write()
    monkeypatch.setattr(restart, "_triggered_restart", True)
    from gptme.tools.base import ToolUse

    monkeypatch.setattr(
        ToolUse,
        "iter_from_content",
        lambda *a, **kw: iter(
            [ToolUse("restart", [], "", start=0, _format="markdown")]
        ),
    )
    restarts = []

    def forbidden_restart(*a, **kw):
        restarts.append(True)
        raise RuntimeError("server process replacement attempted")

    monkeypatch.setattr(restart, "_do_restart", forbidden_restart)
    monkeypatch.setattr(
        "gptme.server.session_step._chat_complete", lambda *a, **kw: ("Hi", None)
    )
    register_hook(
        "parity_restart", HookType.GENERATION_PRE, restart.restart_hook, priority=1000
    )
    try:
        run_step(conversation)
    finally:
        unregister_hook("parity_restart", HookType.GENERATION_PRE)

    assert not restarts
    assert session.last_error is None


# --- Static parity guard ------------------------------------------------------

_SRC = Path(gptme.__file__).parent
_FIRE_RE = re.compile(r"trigger_hook\(\s*HookType\.([A-Z_]+)")

# Hook types the CLI loop fires that the server loop intentionally does not,
# each with the reason. Keep this list short; remove entries as they are wired.
_CLI_ONLY = {
    "LOOP_CONTINUE": (
        "The server loop is client-driven (the client posts /step after each "
        "tool result), so there is no 'decide whether to continue' point. "
        "Background-job and subagent delivery need a server equivalent; "
        "tracked in gptme/gptme#5091."
    ),
}


def _fired(*relpaths: str) -> set[str]:
    found: set[str] = set()
    for rel in relpaths:
        for path in sorted(_SRC.glob(rel)):
            found |= set(_FIRE_RE.findall(path.read_text()))
    return found


def test_cli_hook_fire_sites_have_server_counterpart():
    # GENERATION_PRE is fired by llm.reply(), which the CLI calls and the
    # server bypasses. GENERATION_CHUNK is fired in llm._stream, shared by both.
    assert "GENERATION_PRE" in _fired("llm/__init__.py")
    cli = _fired("chat.py") | {"GENERATION_PRE"}
    # SESSION_END etc. may be fired outside session_step.py, so scan the package.
    server = _fired("server/*.py")
    missing = cli - server - set(_CLI_ONLY)
    assert not missing, (
        f"Hook types fired by the CLI loop but not the server loop: {sorted(missing)}. "
        "Wire them in gptme/server or add them to _CLI_ONLY with a reason."
    )
    # An allowlist entry that is no longer true must be removed.
    stale = {h for h in _CLI_ONLY if h in server or h not in cli}
    assert not stale, f"Stale _CLI_ONLY entries: {sorted(stale)}"
