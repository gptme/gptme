"""Regression tests for the merged-PR follow-through defects in gptme/gptme#5233."""

import errno
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gptme.message import Message

# --- 1: Responses converters must not head-slice instructions (#4206 vs #4166) ---


@pytest.mark.parametrize("as_dicts", [False, True])
def test_responses_instructions_keep_trailing_system_guidance(monkeypatch, as_dicts):
    from gptme.llm import openai_responses

    # Default args bind at definition time, so pass through the patched cap.
    original = openai_responses.clamp_responses_instructions
    monkeypatch.setattr(
        openai_responses,
        "clamp_responses_instructions",
        lambda text: original(text, limit=200),
    )
    messages = [
        Message("system", "\n\n".join(["context" * 3] * 30)),
        Message("system", "WORKSPACE-RULES" * 5),
        Message("user", "hi"),
    ]
    if as_dicts:
        instructions, _ = openai_responses._messages_dicts_to_responses_input(
            [{"role": m.role, "content": m.content} for m in messages]
        )
    else:
        instructions, _ = openai_responses._messages_to_responses_input(messages)
    assert instructions is not None
    assert len(instructions) <= 200
    assert instructions.endswith("WORKSPACE-RULES" * 5)


@pytest.mark.parametrize("as_dicts", [False, True])
def test_responses_instructions_keep_rules_merged_by_prepare_messages(
    monkeypatch, as_dicts
):
    from gptme.llm import openai_responses
    from gptme.logmanager import prepare_messages

    original = openai_responses.clamp_responses_instructions
    monkeypatch.setattr(
        openai_responses,
        "clamp_responses_instructions",
        lambda text: original(text, limit=300),
    )
    context = "CONTEXT-START " + "context " * 100
    rules = "WORKSPACE-RULES" * 5
    messages = prepare_messages(
        [Message("system", context), Message("system", rules), Message("user", "hi")]
    )
    # The upstream merge leaves the converter a single system message.
    assert [m.role for m in messages] == ["system", "user"]

    if as_dicts:
        instructions, _ = openai_responses._messages_dicts_to_responses_input(
            [{"role": m.role, "content": m.content} for m in messages]
        )
    else:
        instructions, _ = openai_responses._messages_to_responses_input(messages)
    assert instructions is not None
    assert len(instructions) == 300
    assert instructions.startswith("CONTEXT-START")
    assert instructions.endswith(rules)
    assert "truncated to fit" in instructions


def test_clamp_responses_instructions_falls_back_to_both_ends():
    from gptme.llm.openai_responses import clamp_responses_instructions

    text = "\n\n".join(["head", "a" * 40, "b" * 40, "tail"])
    clamped = clamp_responses_instructions(text, limit=30, marker="..")
    assert len(clamped) == 30
    assert clamped.startswith("head")
    assert clamped.endswith("tail")


# --- 3: fork-mode subagent usage excludes the forked parent history (#4096) ---


@pytest.mark.parametrize("compacted", [False, True])
def test_fork_subagent_usage_skips_parent_messages(tmp_path: Path, compacted):
    from gptme.tools.subagent.types import Subagent

    def msg(role, content, inp, out):
        return {
            "role": role,
            "content": content,
            "timestamp": "2025-01-01T00:00:00+00:00",
            "metadata": {"usage": {"input_tokens": inp, "output_tokens": out}},
        }

    logdir = tmp_path / "fork-usage"
    logdir.mkdir()
    records = [
        {"role": "user", "content": "parent", "timestamp": "2025-01-01T00:00:00"},
        msg("assistant", "parent reply", 1000, 500),
        msg("assistant", "child reply", 7, 3),
    ]
    (logdir / "conversation.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )
    if compacted:
        # Manual summarization preserves the original records in lossless.jsonl,
        # then appends new messages to both the compacted and lossless logs.
        after = msg("assistant", "child after compaction", 11, 5)
        (logdir / "lossless.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in [*records, after])
        )
        (logdir / "conversation.jsonl").write_text(
            "".join(
                json.dumps(r) + "\n"
                for r in [{"role": "system", "content": "summary"}, after]
            )
        )
    sa = Subagent(
        agent_id="fork-usage",
        prompt="child",
        thread=None,
        logdir=logdir,
        model=None,
        context_mode="fork",
        fork_message_count=2,
    )
    usage = sa._read_usage()
    assert usage["input_tokens"] == (18 if compacted else 7)
    assert usage["output_tokens"] == (8 if compacted else 3)


@pytest.mark.parametrize("clock_backwards", [False, True])
def test_max_time_timeout_reports_duration(
    monkeypatch, tmp_path: Path, clock_backwards
):
    from gptme.tools.subagent import api

    started = time.time() - 3.0
    sa = SimpleNamespace(
        agent_id="slow",
        started_at=started,
        execution_mode="thread",
        process=None,
        logdir=tmp_path,
        parent_logdir=None,
        parent_branch=None,
        is_running=lambda: True,
    )
    captured = {}

    def fake_set(agent_id, result, attempt=None):
        captured["result"] = result
        return True

    monkeypatch.setattr(api, "set_subagent_result_if_absent", fake_set)
    monkeypatch.setattr(api, "notify_completion", lambda *a, **k: None)
    if clock_backwards:
        monkeypatch.setattr(api.time, "time", lambda: started - 1.0)
    api._subagents.append(sa)  # type: ignore[arg-type]
    try:
        api._timeout_subagent("slow", 1.0, expected_run=sa)  # type: ignore[arg-type]
    finally:
        api._subagents.remove(sa)  # type: ignore[arg-type]
    result = captured["result"]
    assert result.status == "timeout"
    if clock_backwards:
        assert result.duration_s == 0.0
    else:
        assert result.duration_s is not None and result.duration_s >= 3.0


# --- 8: non-streaming Responses reports status="incomplete" (#4084) ---


def test_nonstreaming_responses_incomplete_is_not_success(monkeypatch, caplog):
    from gptme.llm import llm_openai

    fake_response = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="partial")],
            )
        ],
        usage=None,
    )
    mock_client = SimpleNamespace(
        responses=SimpleNamespace(create=Mock(return_value=fake_response)),
        chat=SimpleNamespace(completions=SimpleNamespace(create=Mock())),
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setenv("GPTME_OPENAI_RESPONSES_API", "1")
    record = Mock(return_value=None)
    monkeypatch.setattr(llm_openai, "_record_usage", record)

    with caplog.at_level("WARNING"):
        content, _ = llm_openai.chat([Message("user", "hi")], "openai/gpt-5", None)
    assert content == "partial"
    assert record.call_args.kwargs["success"] is False
    assert "incomplete" in caplog.text
    assert "max_output_tokens" in caplog.text


# --- 9: a deeply nested token cache must not crash startup (#4808) ---


def test_deeply_nested_gptme_token_is_treated_as_missing(monkeypatch, tmp_path):
    from gptme.llm import llm_gptme

    token_path = tmp_path / "token.json"
    token_path.write_text("[" * 100_000 + "]" * 100_000)
    monkeypatch.setattr(llm_gptme, "_get_token_path", lambda url=None: token_path)
    assert llm_gptme._load_token("https://example.invalid") is None


# --- 10: one bad path in a batch read must not abort the others (#4790) ---


def test_batch_read_survives_enametoolong(monkeypatch, tmp_path):
    from gptme.tools.read import execute_read

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ok.txt").write_text("hello\n")
    bad = "bad.txt"
    original_exists = Path.exists

    def exists(self, *args, **kwargs):
        if self.name == bad:
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return original_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)
    contents = [m.content for m in execute_read(f"{bad}\nok.txt", [], None)]
    joined = "\n".join(contents)
    assert "hello" in joined
    assert "File name too long" in joined


# --- 14: Claude 5.5 cache-read prices and Fable 5.2 registration (#4125) ---


@pytest.mark.parametrize(
    ("model", "cache_read"),
    [
        ("anthropic/claude-opus-5-5", 0.20),
        ("anthropic/claude-sonnet-5-5", 0.10),
        ("anthropic/claude-fable-5-2", 0.20),
    ],
)
def test_claude_cache_read_prices(model, cache_read):
    from gptme.llm.models import get_model

    meta = get_model(model)
    assert meta.price_cache_read == pytest.approx(cache_read)


def test_fable_5_2_is_registered_with_its_own_prices():
    from gptme.llm.models import get_model

    meta = get_model("anthropic/claude-fable-5-2")
    assert meta.model == "claude-fable-5-2"
    assert (meta.price_input, meta.price_output) == (8, 40)


# --- 15: MCP tool names containing "/" are not tool-file paths (#4265) ---


@pytest.mark.parametrize(
    ("entry", "is_path"),
    [
        ("docs/team.echo_text", False),
        ("shell", False),
        ("my_tool.py", True),
        ("sub/dir/tool.py", True),
        ("./tool", True),
        ("../tools/x", True),
        ("~/tools/x.py", True),
        ("/abs/tool.py", True),
        ("C:\\tools\\x.py", True),
    ],
)
def test_is_tool_file_path(entry, is_path):
    from gptme.tools._allowlist import is_tool_file_path

    assert is_tool_file_path(entry) is is_path
