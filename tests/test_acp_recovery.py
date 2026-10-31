"""Offline ACP regression: previews cannot be reset, so never replay a reply."""

import asyncio
import importlib
import threading
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest

from gptme import llm
from gptme.acp.agent import GptmeAgent, _import_acp
from gptme.llm import recovery
from gptme.message import Message, MessageMetadata
from gptme.tools import init_tools


@pytest.mark.parametrize("partial_output", [False, True])
def test_acp_provider_failure_does_not_replay(
    monkeypatch: pytest.MonkeyPatch, partial_output: bool
) -> None:
    """Exercise the real executor, chat.step, reply and ACP preview/error path."""
    if not _import_acp():
        pytest.skip("acp not installed")

    init_tools(allowlist=["shell"])
    chat = importlib.import_module("gptme.chat")
    monkeypatch.setattr(llm, "init_llm", lambda *a: None)
    monkeypatch.setattr(llm, "is_output_quiet", lambda: True)
    monkeypatch.setattr(
        llm, "get_model", lambda *a: Mock(supports_parallel_tool_calls=False)
    )
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.setattr("gptme.hooks.registry.trigger_hook", lambda *a, **kw: iter(()))
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 3)
    monkeypatch.setattr(recovery, "backoff_wait", lambda *a: False)

    agent = GptmeAgent()
    conn = MagicMock()
    conn.session_update = AsyncMock()
    agent._conn = conn
    agent._session_commands_advertised.add("s1")
    agent._session_models["s1"] = "openai/gpt-4o"
    log = MagicMock()
    log.log = [Message("user", "hello")]
    log.workspace = None
    log.logdir = None
    agent._registry.create("s1", log=log)

    budgets: list[recovery.RecoveryBudget] = []
    worker_threads: list[int] = []
    caller_thread = threading.get_ident()

    def stream(messages: object, model: str, tools: object, **kwargs: object) -> object:
        budget = recovery.get_recovery_budget()
        assert budget is not None, "disabling replay must retain provider accounting"
        assert recovery.recovery_active(), "adapter-local retries must stay suppressed"
        recovery.reserve_recovery_request()
        budgets.append(budget)
        worker_threads.append(threading.get_ident())

        def chunks() -> Generator[str, None, MessageMetadata | None]:
            if len(budgets) == 1:
                if partial_output:
                    yield "failed ACP preview\n"
                raise httpx.RemoteProtocolError("fixture ACP stream cut")
            yield "replacement response must never be streamed\n"
            return {"model": model}

        return llm._StreamWithMetadata(chunks(), model)

    monkeypatch.setattr(llm, "_stream", stream)
    result = asyncio.run(
        agent.prompt(prompt=[{"type": "text", "text": "hello"}], session_id="s1")
    )

    assert len(budgets) == 1, "ACP must not replay a generation without preview reset"
    assert budgets[0].requests == 1
    assert worker_threads[0] != caller_thread
    assert result.stop_reason == "cancelled"
    updates = "\n".join(
        str(call.kwargs["update"]) for call in conn.session_update.await_args_list
    )
    assert "fixture ACP stream cut" in updates
    assert ("failed ACP preview" in updates) is partial_output
    assert "replacement response" not in updates
    assert all(call.args[0].role != "assistant" for call in log.append.call_args_list)
    assert recovery.get_recovery_budget() is None
