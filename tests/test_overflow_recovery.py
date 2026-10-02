"""Overflow retries preserve views, whole turns, and the output boundary."""

import importlib
from uuid import uuid4

import httpx
import pytest

from gptme.llm import mark_llm_reply_origin
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.util.context_measurement import input_log_digest


def overflow() -> httpx.HTTPStatusError:
    error = httpx.HTTPStatusError(
        "maximum context length exceeded",
        request=httpx.Request("POST", "https://example.test"),
        response=httpx.Response(400),
    )
    mark_llm_reply_origin(error)
    return error


def history() -> list[Message]:
    return [
        Message("system", "System prompt"),
        Message("user", "Original task"),
        Message("assistant", "Old response " * 200),
        Message("system", "Old result " * 200),
        Message("assistant", "More history " * 200),
        Message("system", "More results " * 200),
        Message("user", "Continue"),
    ]


def cli_reply(manager: LogManager) -> Message:
    chat = importlib.import_module("gptme.chat")
    return chat._reply_with_overflow_recovery(
        log=manager.log,
        msgs=manager.log.messages,
        model="openai/gpt-4",
        stream=False,
        tools=None,
        workspace=None,
        output_schema=None,
        on_token=None,
        on_thinking=None,
        logdir=manager.logdir,
    )


def test_cli_recovers_after_two_context_rejections(tmp_path, monkeypatch):
    chat = importlib.import_module("gptme.chat")
    manager = LogManager(history(), logdir=tmp_path / "conversation")
    original = manager.log.messages.copy()
    calls = []

    def generate(messages, *args, **kwargs):
        calls.append(messages.copy())
        if len(calls) <= 2:
            raise overflow()
        return Message(
            "assistant", "Recovered", metadata={"usage": {"input_tokens": 100}}
        )

    monkeypatch.setattr(chat, "reply", generate)
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages[:2] + active.log.messages[4:],
    )
    result = cli_reply(manager)
    assert len(calls) == 3
    assert [len(m) for m in calls] == [7, 5, 3]
    assert manager.log.messages[:2] == original[:2]
    assert manager.log.messages[-1] == original[-1]
    assert result.metadata is not None
    assert result.metadata["input_log_digest"] == input_log_digest(manager.log.messages)
    manager.switch_to_master()
    assert manager.log.messages == original


def test_cli_restores_original_view_on_retry_failure(tmp_path, monkeypatch):
    chat = importlib.import_module("gptme.chat")
    manager = LogManager(history(), logdir=tmp_path / "conversation")
    manager.create_view("existing", history()[:-2] + history()[-1:])
    manager.switch_view("existing")
    original = manager.log.messages.copy()
    calls = 0

    def generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise overflow()
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(chat, "reply", generate)
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages[:2] + active.log.messages[-1:],
    )
    with pytest.raises(RuntimeError, match="provider unavailable"):
        cli_reply(manager)
    assert manager.current_view == "existing"
    assert manager.log.messages == original


@pytest.mark.parametrize(
    ("stream", "partial"), [(False, False), (True, False), (True, True)]
)
def test_server_recovers_before_output_and_anchors_retry(
    client, tmp_path, monkeypatch, stream, partial
):
    pytest.importorskip("flask")
    from gptme.server import session_step
    from gptme.server.session_models import SessionManager

    name = f"test-overflow-{uuid4().hex}"
    response = client.put(
        f"/api/v2/conversations/{name}",
        json={
            "prompt": "System prompt",
            "config": {"chat": {"workspace": str(tmp_path)}},
        },
    )
    assert response.status_code == 200
    session = SessionManager.get_session(response.get_json()["session_id"])
    assert session is not None
    manager = LogManager.load(name, lock=False)
    for message in history()[1:]:
        manager.append(message)
    manager.write()
    calls = []

    def complete(messages, *args, **kwargs):
        calls.append(messages.copy())
        if len(calls) == 1:
            raise overflow()
        return "Recovered", {"usage": {"input_tokens": 100}}

    class Stream:
        metadata = {"usage": {"input_tokens": 100}}

        def __init__(self, messages):
            self.messages = messages

        def __iter__(self):
            if partial:
                calls.append(self.messages.copy())
                yield "Visible prefix that reaches SSE\n"
                raise overflow()
            output, _ = complete(self.messages)
            yield output

    monkeypatch.setattr(session_step, "_chat_complete", complete)
    monkeypatch.setattr(
        session_step, "_stream", lambda messages, *a, **kw: Stream(messages)
    )
    monkeypatch.setattr(session_step, "trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(
        session_step, "_try_auto_name_and_notify", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages[:2] + active.log.messages[-1:],
    )
    session.generating = True
    try:
        session_step.step(name, session, "openai/gpt-4", tmp_path, stream=stream)
        reloaded = LogManager.load(name, lock=False)
        if partial:
            assert len(calls) == 1
            assert session.last_error is not None
            assert reloaded.current_view is None
            assert not (reloaded.logdir / "compaction.jsonl").exists()
            return
        assert len(calls) == 2
        assert session.last_error is None
        assert reloaded.current_view is not None
        result = reloaded.log.messages[-1]
        assert result.content == "Recovered"
        assert result.metadata is not None
        assert result.metadata["input_log_messages"] == len(reloaded.log.messages) - 1
        assert result.metadata["input_log_digest"] == input_log_digest(
            reloaded.log.messages[:-1]
        )
    finally:
        SessionManager.remove_session(session.id)


@pytest.mark.parametrize("format", ["markdown", "xml", "tool"])
@pytest.mark.parametrize("protected", ["head", "pinned-result", "latest"])
def test_drop_oldest_preserves_tool_groups_and_reasoning(
    monkeypatch, format, protected
):
    from gptme.tools import ToolUse
    from gptme.tools.autocompact import recovery

    calls = {format: ToolUse("shell", [], "echo hello").to_output(format)}
    old_call = Message("assistant", "<think>keep reasoning</think>\n" + calls[format])
    old_result = Message("system", "hello", call_id="old-call")
    second_result = Message("system", "second result", call_id="second-call")
    messages = [*history()[:2], old_call, old_result, second_result, *history()[4:]]
    if protected == "head":
        monkeypatch.setattr(recovery, "_get_keep_head", lambda: 3)
    elif protected == "pinned-result":
        messages[3] = messages[3].replace(pinned=True)
    else:
        messages = [*history()[:2], *history()[4:], old_call, old_result, second_result]
    retained = recovery._drop_oldest_turn(messages)
    assert old_call in retained
    assert all(message in retained for message in messages if message.call_id)
    assert retained[:2] == messages[:2]
    assert retained[-1] is messages[-1]
    assert old_call.content == "<think>keep reasoning</think>\n" + calls[format]


def test_drop_oldest_removes_all_results_with_their_call():
    from gptme.tools.autocompact.recovery import _drop_oldest_turn

    call = Message("assistant", "```shell\necho hi\n```", call_id="call")
    results = [
        Message("system", "hi", call_id="a"),
        Message("system", "hi again", call_id="b"),
    ]
    original = [*history()[:2], call, *results, Message("user", "Continue")]
    assert _drop_oldest_turn(original) == [*original[:2], original[-1]]


def test_cli_drops_old_turns_when_trim_is_a_noop(tmp_path, monkeypatch):
    chat = importlib.import_module("gptme.chat")
    manager = LogManager(history(), logdir=tmp_path / "conversation")
    calls = []

    def generate(messages, *args, **kwargs):
        calls.append(messages.copy())
        if len(calls) == 1:
            raise overflow()
        return Message("assistant", "Recovered")

    monkeypatch.setattr(chat, "reply", generate)
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages,
    )
    assert cli_reply(manager).content == "Recovered"
    assert len(calls) == 2
    assert len(calls[-1]) < len(calls[0])


def test_exhausted_recovery_restores_existing_view(tmp_path, monkeypatch):
    chat = importlib.import_module("gptme.chat")
    manager = LogManager(history(), logdir=tmp_path / "conversation")
    manager.create_view("existing", history())
    manager.switch_view("existing")
    original = manager.log.messages.copy()
    calls = []

    def generate(messages, *args, **kwargs):
        calls.append(messages.copy())
        raise overflow()

    monkeypatch.setattr(chat, "reply", generate)
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages,
    )
    with pytest.raises(httpx.HTTPStatusError):
        cli_reply(manager)
    assert manager.current_view == "existing"
    assert manager.log.messages == original
    assert len(calls) <= 9
    assert all(len(after) < len(before) for before, after in zip(calls, calls[1:]))


def test_trim_of_filtered_message_continues_dropping(tmp_path, monkeypatch):
    from gptme.tools.autocompact.recovery import recover_reply

    original = [
        *history()[:2],
        Message("assistant", "Invisible " * 200, ui_only=True),
        *history()[4:],
    ]
    manager = LogManager(original, logdir=tmp_path / "conversation")
    calls = []

    def prepare(messages):
        return [message for message in messages if not message.ui_only]

    def generate(messages):
        calls.append(messages.copy())
        if len(calls) == 1:
            raise overflow()
        return Message("assistant", "Recovered")

    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: [
            *active.log.messages[:2],
            active.log.messages[2].replace(content="Short"),
            *active.log.messages[3:],
        ],
    )
    result = recover_reply(
        manager, prepare(original), "openai/gpt-4", generate, prepare
    )
    assert result.content == "Recovered"
    assert len(calls) == 2
    assert len(calls[1]) < len(calls[0])


def test_signed_thinking_details_survive_overflow_trim(tmp_path, monkeypatch):
    from gptme.llm.models import ModelMeta
    from gptme.tools.autocompact.recovery import compact_for_overflow

    tiny = ModelMeta(provider="unknown", model="gpt-4", context=300)
    monkeypatch.setattr(
        "gptme.tools.autocompact.engine.get_default_model", lambda: tiny
    )
    reasoning = (
        "<think><details>"
        + "private reasoning " * 400
        + "</details></think><!-- think-sig: signature -->"
    )
    messages = [
        *history()[:2],
        Message("assistant", reasoning),
        Message("user", "Continue"),
    ]
    manager = LogManager(messages, logdir=tmp_path / "conversation")
    compacted = compact_for_overflow(manager)
    assert compacted[2].content == reasoning
    assert not compacted[2].pinned  # It remains droppable as a complete step.
    assert compacted[0].pinned
    assert compacted[1].pinned
    assert manager.log.messages == messages


@pytest.mark.parametrize("revocation", ["interrupt", "replacement"])
def test_server_revoked_overflow_does_not_retry_or_publish(
    client, tmp_path, monkeypatch, revocation
):
    from gptme.server import session_step
    from gptme.server.session_models import SessionManager

    name = f"test-revoked-overflow-{uuid4().hex}"
    response = client.put(
        f"/api/v2/conversations/{name}",
        json={"prompt": "System", "config": {"chat": {"workspace": str(tmp_path)}}},
    )
    assert response.status_code == 200
    session = SessionManager.get_session(response.get_json()["session_id"])
    assert session is not None
    manager = LogManager.load(name, lock=False)
    for message in history()[1:]:
        manager.append(message)
    manager.write()
    before = manager.log.messages.copy()
    calls = []
    events = []

    def complete(messages, *args, **kwargs):
        calls.append(messages.copy())
        if revocation == "interrupt":
            session.interrupted = True
        else:
            session.step_seq += 1
        raise overflow()

    monkeypatch.setattr(session_step, "_chat_complete", complete)
    monkeypatch.setattr(session_step, "trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(
        SessionManager, "add_event", lambda cid, event: events.append(event)
    )
    session.generating = True
    try:
        session_step.step(name, session, "openai/gpt-4", tmp_path, stream=False)
        reloaded = LogManager.load(name, lock=False)
        assert len(calls) == 1
        assert reloaded.current_view is None
        assert reloaded.log.messages == before
        assert session.last_error is None
        assert not (manager.logdir / "compaction.jsonl").exists()
        assert not any(
            event["type"] in {"error", "generation_complete"} for event in events
        )
        if revocation == "replacement":
            assert session.generating  # Old step cannot release the new reservation.
            assert not any(event["type"] == "step_complete" for event in events)
    finally:
        SessionManager.remove_session(session.id)


def test_many_context_rejections_are_bounded_to_eight_retries(tmp_path, monkeypatch):
    from gptme.tools.autocompact.events import read_compaction_events
    from gptme.tools.autocompact.recovery import recover_reply

    messages = history()[:2]
    for index in range(128):
        messages.extend(
            [
                Message("assistant", f"old {index} " * 50),
                Message("system", "result " * 50),
            ]
        )
    messages.append(Message("user", "Continue"))
    manager = LogManager(messages, logdir=tmp_path / "conversation")
    calls = []

    def generate(prepared):
        calls.append(prepared.copy())
        raise overflow()

    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages,
    )
    with pytest.raises(httpx.HTTPStatusError):
        recover_reply(
            manager, messages, "openai/gpt-4", generate, lambda messages: messages
        )
    assert len(calls) == 9
    assert all(len(after) < len(before) for before, after in zip(calls, calls[1:]))
    assert manager.current_view is None
    assert manager.log.messages == messages
    assert len(read_compaction_events(manager.logdir)) == 8


@pytest.mark.parametrize("revocation", ["interrupt", "replacement"])
def test_server_revoked_during_retry_restores_only_its_own_epoch(
    client, tmp_path, monkeypatch, revocation
):
    from gptme.server import session_step
    from gptme.server.session_models import SessionManager

    name = f"test-retry-revoked-{uuid4().hex}"
    response = client.put(
        f"/api/v2/conversations/{name}",
        json={"prompt": "System", "config": {"chat": {"workspace": str(tmp_path)}}},
    )
    assert response.status_code == 200
    session = SessionManager.get_session(response.get_json()["session_id"])
    assert session is not None
    manager = LogManager.load(name, lock=False)
    for message in history()[1:]:
        manager.append(message)
    manager.write()
    manager.create_view("original", manager.log.messages)
    manager.switch_view("original")
    calls = []

    def complete(messages, *args, **kwargs):
        calls.append(messages.copy())
        if len(calls) == 1:
            raise overflow()
        if revocation == "interrupt":
            session.interrupted = True
        else:
            with session.step_lock:
                session.step_seq += 1
                replacement = LogManager.load(name, lock=False)
                replacement.create_view("replacement", replacement.log.messages)
                replacement.switch_view("replacement")
        raise RuntimeError("cancelled provider request")

    monkeypatch.setattr(session_step, "_chat_complete", complete)
    monkeypatch.setattr(session_step, "trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages[:2] + active.log.messages[-1:],
    )
    session.generating = True
    try:
        session_step.step(name, session, "openai/gpt-4", tmp_path, stream=False)
        reloaded = LogManager.load(name, lock=False)
        assert len(calls) == 2
        assert reloaded.current_view == (
            "original" if revocation == "interrupt" else "replacement"
        )
        assert session.last_error is None
        assert reloaded.log.messages[-1].role == "user"
    finally:
        SessionManager.remove_session(session.id)


def test_pinned_state_restored_by_position_avoids_collision(tmp_path, monkeypatch):
    """Two reasoning messages sharing the same content+timestamp must not collide."""
    from datetime import datetime, timezone

    from gptme.llm.models import ModelMeta
    from gptme.tools.autocompact.recovery import compact_for_overflow

    tiny = ModelMeta(provider="unknown", model="gpt-4", context=300)
    monkeypatch.setattr(
        "gptme.tools.autocompact.engine.get_default_model", lambda: tiny
    )
    # Force same timestamp so the old dict key (ts, content, role, call_id) collides.
    shared_ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    reasoning = "<think>identical reasoning " * 10 + "</think>"
    droppable_msg = Message("assistant", reasoning, timestamp=shared_ts, pinned=False)
    pinned_msg = Message("assistant", reasoning, timestamp=shared_ts, pinned=True)
    messages = [
        Message("system", "System prompt"),
        Message("user", "Original task"),
        droppable_msg,
        Message("user", "First continue"),
        pinned_msg,
        Message("user", "Second continue " * 20),
    ]
    manager = LogManager(messages, logdir=tmp_path / "conversation")
    compacted = compact_for_overflow(manager)
    # Both reasoning messages are preserved (pinned during trim) and appear in order
    reasoning_msgs = [m for m in compacted if "<think>" in m.content]
    assert len(reasoning_msgs) == 2
    # Position-based restore: first is droppable, second is pinned
    assert not reasoning_msgs[0].pinned, "First reasoning message must remain droppable"
    assert reasoning_msgs[1].pinned, "Second reasoning message must remain pinned"


@pytest.mark.parametrize("revocation", ["interrupt", "replacement"])
def test_server_revoked_after_retry_does_not_commit_reply(
    client, tmp_path, monkeypatch, revocation
):
    """A successful retry that races an epoch revocation must not commit its reply."""
    from gptme.server import session_step
    from gptme.server.session_models import SessionManager

    name = f"test-revoked-after-retry-{uuid4().hex}"
    response = client.put(
        f"/api/v2/conversations/{name}",
        json={"prompt": "System", "config": {"chat": {"workspace": str(tmp_path)}}},
    )
    assert response.status_code == 200
    session = SessionManager.get_session(response.get_json()["session_id"])
    assert session is not None
    manager = LogManager.load(name, lock=False)
    for message in history()[1:]:
        manager.append(message)
    manager.write()
    calls = []

    def complete(messages, *args, **kwargs):
        calls.append(messages.copy())
        if len(calls) == 1:
            raise overflow()
        # Retry succeeded — revoke epoch after the LLM responds but before commit
        if revocation == "interrupt":
            session.interrupted = True
        else:
            with session.step_lock:
                session.step_seq += 1
        return "Recovered after revocation", None

    monkeypatch.setattr(session_step, "_chat_complete", complete)
    monkeypatch.setattr(session_step, "trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active: active.log.messages[:2] + active.log.messages[-1:],
    )
    session.generating = True
    try:
        session_step.step(name, session, "openai/gpt-4", tmp_path, stream=False)
        reloaded = LogManager.load(name, lock=False)
        assert len(calls) == 2
        # Reply must NOT be committed — the epoch was revoked
        assert reloaded.log.messages[-1].role == "user"
        assert all(
            "Recovered" not in m.content
            for m in reloaded.log.messages
            if m.role == "assistant"
        )
    finally:
        SessionManager.remove_session(session.id)
