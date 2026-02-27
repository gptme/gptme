"""Tests for ACP server session runtime wrapper."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _DummyBlock:
    def __init__(self, text: str):
        self.text = text


class _DummyClient:
    def __init__(self, workspace: Path, **kwargs: Any) -> None:
        self.workspace = workspace
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self.prompt_calls: list[tuple[str, str]] = []

    async def __aenter__(self):
        self.started = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

    async def new_session(self, cwd: str | Path | None = None, **kwargs: Any) -> str:
        return "sess-test"

    async def prompt(self, session_id: str, message: str):
        self.prompt_calls.append((session_id, message))
        return SimpleNamespace(output=[_DummyBlock("hello"), _DummyBlock(" world")])


@pytest.mark.parametrize(
    ("resp", "expected"),
    [
        ({"output": [{"text": "abc"}, {"text": "123"}]}, "abc123"),
        (SimpleNamespace(output=[_DummyBlock("a"), _DummyBlock("b")]), "ab"),
        ({"text": "fallback"}, "fallback"),
        (SimpleNamespace(text="fallback2"), "fallback2"),
        ({"output": [{"no_text": True}]}, ""),
    ],
)
def test_extract_text_from_prompt_response(resp, expected):
    from gptme.server.acp_session_runtime import extract_text_from_prompt_response

    assert extract_text_from_prompt_response(resp) == expected


def test_runtime_lifecycle_and_prompt(monkeypatch, tmp_path):
    import gptme.server.acp_session_runtime as mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mod, "GptmeAcpClient", _factory)

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)

    # Explicit start + prompt
    _run(runtime.start())
    assert runtime.session_id == "sess-test"
    assert len(created) == 1
    assert created[0].started is True

    text, raw = _run(runtime.prompt("hi there"))
    assert text == "hello world"
    assert created[0].prompt_calls == [("sess-test", "hi there")]
    assert raw is not None

    # Idempotent start should not create additional clients
    _run(runtime.start())
    assert len(created) == 1

    _run(runtime.close())
    assert created[0].closed is True
    assert runtime.session_id is None


def test_runtime_lazy_start_on_prompt(monkeypatch, tmp_path):
    import gptme.server.acp_session_runtime as mod

    created: list[_DummyClient] = []

    def _factory(*args, **kwargs):
        c = _DummyClient(*args, **kwargs)
        created.append(c)
        return c

    monkeypatch.setattr(mod, "GptmeAcpClient", _factory)

    runtime = mod.AcpSessionRuntime(workspace=tmp_path)
    text, _ = _run(runtime.prompt("start lazily"))

    assert text == "hello world"
    assert runtime.session_id == "sess-test"
    assert len(created) == 1
