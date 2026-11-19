"""Conversation effort is persistent, context-local, and provider aware."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from gptme.config import ChatConfig, Config, get_config, set_config
from gptme.llm.llm_openai import extra_body
from gptme.llm.models import get_model


def test_conversation_effort_overrides_environment_without_leaking(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "medium")
    both_ready = Barrier(2)

    def request(level):
        config = ChatConfig.from_dict({"chat": {"reasoning_effort": level}})
        config._logdir = tmp_path / level
        config.save()
        assert config._logdir is not None
        set_config(Config(chat=ChatConfig.from_logdir(config._logdir)))
        both_ready.wait(timeout=5)
        return extra_body("openai", get_model("openai/o3"))

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(request, ["low", "high"]))
    assert results == [{"reasoning_effort": "low"}, {"reasoning_effort": "high"}]
    assert get_config().get_env("THINKING_EFFORT") == "medium"


def test_default_effort_inherits(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "high")
    set_config(Config(chat=ChatConfig()))
    assert extra_body("openai", get_model("openai/o3"))["reasoning_effort"] == "high"


@pytest.mark.parametrize("value", [True, 3, "bogus"])
def test_invalid_effort_rejected(value):
    with pytest.raises(ValueError, match="reasoning_effort"):
        ChatConfig.from_dict({"chat": {"reasoning_effort": value}})


@pytest.mark.parametrize(
    ("model", "levels"),
    [
        ("openai/o3", ["low", "medium", "high"]),
        ("openai/gpt-4o", []),
        ("moonshot/kimi-k3", ["low", "high", "max"]),
        ("openai-subscription/gpt-6.1-sol", ["low", "medium", "high", "xhigh"]),
        ("anthropic/claude-opus-5-5", ["low", "medium", "high", "xhigh", "max"]),
    ],
)
def test_model_effort_capabilities(model, levels):
    from gptme.llm.models.effort import reasoning_effort_levels

    assert reasoning_effort_levels(get_model(model)) == levels


def test_subscription_effort_and_metadata_use_conversation_choice():
    from gptme.llm.llm_openai_subscription import _codex_model_and_effort

    set_config(Config(chat=ChatConfig(reasoning_effort="high")))
    assert _codex_model_and_effort("gpt-6.1-sol:low") == ("gpt-6.1-sol", "high")
    set_config(Config(chat=ChatConfig(reasoning_effort="")))
    assert _codex_model_and_effort("gpt-6.1-sol:low") == ("gpt-6.1-sol", "low")


def test_step_persists_effort_and_default_and_rejects_model_switch(client):
    from unittest.mock import patch

    from gptme.server.session_models import SessionManager

    cid = "effort-api"
    response = client.put(f"/api/v2/conversations/{cid}", json={"prompt": "test"})
    assert response.status_code == 200
    sid = response.get_json()["session_id"]
    session = SessionManager.get_session(sid)
    assert session is not None
    captured = []

    def dispatch(**kwargs):
        from gptme.dirs import get_logs_dir

        cfg = Config.from_logdir(get_logs_dir() / cid)
        set_config(cfg)
        captured.append(extra_body("openai", get_model(kwargs["model"])))
        session.generating = False

    with patch("gptme.server.api_v2_sessions._start_step_thread", side_effect=dispatch):
        response = client.post(
            f"/api/v2/conversations/{cid}/step",
            json={
                "session_id": sid,
                "model": "openai/o3",
                "reasoning_effort": "low",
            },
        )
        assert response.status_code == 200, response.get_json()
        assert captured[-1] == {"reasoning_effort": "low"}
        assert (
            client.get(f"/api/v2/conversations/{cid}/config").get_json()["chat"][
                "reasoning_effort"
            ]
            == "low"
        )
        # Tool continuation/no new selection retains the saved conversation setting.
        response = client.post(
            f"/api/v2/conversations/{cid}/step", json={"session_id": sid}
        )
        assert response.status_code == 200
        assert captured[-1] == {"reasoning_effort": "low"}
        response = client.post(
            f"/api/v2/conversations/{cid}/step",
            json={
                "session_id": sid,
                "model": "openai/gpt-4o",
            },
        )
        assert response.status_code == 400
        assert "not supported" in response.get_json()["error"]
        assert not session.generating
        response = client.post(
            f"/api/v2/conversations/{cid}/step",
            json={
                "session_id": sid,
                "model": "openai/gpt-4o",
                "reasoning_effort": "",
            },
        )
        assert response.status_code == 200
        assert captured[-1] == {}
        assert (
            client.get(f"/api/v2/conversations/{cid}/config").get_json()["chat"][
                "reasoning_effort"
            ]
            == ""
        )


def test_patch_can_reset_effort_to_inherit(client):
    cid = "effort-patch"
    assert (
        client.put(
            f"/api/v2/conversations/{cid}",
            json={
                "prompt": "test",
                "config": {
                    "chat": {"model": "openai/o3", "reasoning_effort": "high"},
                },
            },
        ).status_code
        == 200
    )
    response = client.patch(
        f"/api/v2/conversations/{cid}/config", json={"chat": {"reasoning_effort": ""}}
    )
    assert response.status_code == 200, response.get_json()
    assert (
        client.get(f"/api/v2/conversations/{cid}/config").get_json()["chat"][
            "reasoning_effort"
        ]
        == ""
    )


@pytest.mark.parametrize("with_tool", [False, True])
def test_real_step_applies_effort_and_records_metadata(client, monkeypatch, with_tool):
    """Exercise the HTTP -> real worker -> provider request -> stored reply path."""
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock

    from gptme.llm import llm_openai
    from gptme.logmanager import LogManager
    from gptme.server.session_models import SessionManager

    cid = "effort-real-step"
    response = client.put(
        f"/api/v2/conversations/{cid}",
        json={
            "prompt": "test",
            "config": {
                "chat": {
                    "model": "openai/o3",
                    "tools": ["shell"] if with_tool else [],
                    "stream": False,
                    "tool_format": "markdown",
                },
            },
        },
    )
    assert response.status_code == 200
    sid = response.get_json()["session_id"]
    assert (
        client.post(
            f"/api/v2/conversations/{cid}", json={"role": "user", "content": "hi"}
        ).status_code
        == 200
    )
    completion = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="```shell\necho effort-continuation\n```"
                    if with_tool
                    else "ok",
                    tool_calls=None,
                ),
            )
        ],
    )
    create = Mock(return_value=SimpleNamespace(parse=lambda: completion, headers={}))
    monkeypatch.setattr(
        llm_openai,
        "get_client",
        lambda provider: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    with_raw_response=SimpleNamespace(create=create)
                )
            ),
        ),
    )
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)
    monkeypatch.setattr("gptme.server.session_step._chat_complete", llm_openai.chat)
    monkeypatch.setattr(
        "gptme.server.session_step._try_auto_name_and_notify", lambda *a, **kw: None
    )
    response = client.post(
        f"/api/v2/conversations/{cid}/step",
        json={
            "session_id": sid,
            "reasoning_effort": "high",
            "stream": False,
        },
    )
    assert response.status_code == 200, response.get_json()
    deadline = time.monotonic() + 5
    session = SessionManager.get_session(sid)
    assert session is not None
    while session.generating and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not session.generating
    assert create.call_args.kwargs["extra_body"] == {"reasoning_effort": "high"}
    reply = next(
        m
        for m in LogManager.load(cid, lock=False).log.messages
        if m.role == "assistant"
    )
    assert reply.metadata is not None
    assert reply.metadata["reasoning_effort"] == "high"
    if with_tool:
        assert session.pending_tools
        tool_id = next(iter(session.pending_tools))
        completion.choices[0].message.content = "Tool finished."

        class ReplyStream:
            def __init__(self, messages, model, tools=None, **kwargs):
                self.text, self.metadata = llm_openai.chat(
                    messages, model, tools, **kwargs
                )

            def __iter__(self):
                yield self.text

        from gptme.message import Message

        monkeypatch.setattr("gptme.server.session_step._stream", ReplyStream)
        monkeypatch.setattr(
            "gptme.tools.ToolUse.execute",
            lambda *a, **kw: iter([Message("system", "Tool executed.")]),
        )
        response = client.post(
            f"/api/v2/conversations/{cid}/tool/confirm",
            json={
                "session_id": sid,
                "tool_id": tool_id,
                "action": "confirm",
            },
        )
        assert response.status_code == 200, response.get_json()
        deadline = time.monotonic() + 5
        while (
            create.call_count < 2 or session.generating or session._executing_tools
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not session.generating
        assert not session._executing_tools
        assert create.call_count == 2
        assert create.call_args.kwargs["extra_body"] == {"reasoning_effort": "high"}
        replies = [
            m
            for m in LogManager.load(cid, lock=False).log.messages
            if m.role == "assistant"
        ]
        assert len(replies) == 2
        assert all(
            m.metadata and m.metadata["reasoning_effort"] == "high" for m in replies
        )


def test_concurrent_api_workers_keep_effort_isolated(client, monkeypatch):
    """Two real server workers overlap at the provider boundary."""
    import time

    from gptme.logmanager import LogManager
    from gptme.server.session_models import SessionManager

    both_ready = Barrier(2)
    calls = {}

    def provider_request(messages, model, tools=None, **kwargs):
        both_ready.wait(timeout=5)
        level = extra_body("openai", get_model(model))["reasoning_effort"]
        calls[next(m.content for m in messages if m.role == "user")] = level
        return "ok", {"reasoning_effort": level}

    monkeypatch.setattr("gptme.server.session_step._chat_complete", provider_request)
    monkeypatch.setattr(
        "gptme.server.session_step._try_auto_name_and_notify", lambda *a, **kw: None
    )
    sessions = []
    for level in ("low", "high"):
        cid = f"effort-concurrent-{level}"
        response = client.put(
            f"/api/v2/conversations/{cid}",
            json={
                "prompt": "test",
                "config": {
                    "chat": {"model": "openai/o3", "tools": [], "stream": False},
                },
            },
        )
        assert response.status_code == 200
        sid = response.get_json()["session_id"]
        sessions.append((cid, sid, level))
        assert (
            client.post(
                f"/api/v2/conversations/{cid}", json={"role": "user", "content": level}
            ).status_code
            == 200
        )
    app = client.application

    def send_step(item):
        cid, sid, level = item
        with app.test_client() as worker_client:
            response = worker_client.post(
                f"/api/v2/conversations/{cid}/step",
                json={"session_id": sid, "reasoning_effort": level, "stream": False},
            )
            assert response.status_code == 200, response.get_json()

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(send_step, sessions))
    for cid, sid, level in sessions:
        session = SessionManager.get_session(sid)
        assert session is not None
        deadline = time.monotonic() + 6
        while session.generating and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not session.generating
        reply = next(
            m
            for m in LogManager.load(cid, lock=False).log.messages
            if m.role == "assistant"
        )
        assert reply.metadata and reply.metadata["reasoning_effort"] == level
    assert calls == {"low": "low", "high": "high"}
