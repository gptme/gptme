"""Tests for gptme-util batch command."""

from __future__ import annotations

import json
import subprocess

from click.testing import CliRunner

from gptme.cli import cmd_batch
from gptme.cli.util import main as util_main


def _jsonl(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_batch_skips_empty_stdin_lines(monkeypatch):
    calls: list[dict] = []

    def fake_run_one_prompt(**kwargs):
        calls.append(kwargs)
        return {
            "index": kwargs["index"],
            "prompt": kwargs["prompt"],
            "exit_reason": "done",
            "tokens": 0,
            "duration_s": 0.0,
            "tool_calls": 0,
        }

    monkeypatch.setattr(cmd_batch, "_run_one_prompt", fake_run_one_prompt)

    runner = CliRunner()
    result = runner.invoke(
        util_main,
        [
            "batch",
            "--jsonl-only",
            "--model",
            "test/model",
            "--max-turns",
            "3",
            "--timeout",
            "7",
        ],
        input="first\n\n  \nsecond\n",
    )

    assert result.exit_code == 0, result.output
    assert _jsonl(result.output) == [
        {
            "duration_s": 0.0,
            "exit_reason": "done",
            "index": 0,
            "prompt": "first",
            "tokens": 0,
            "tool_calls": 0,
        },
        {
            "duration_s": 0.0,
            "exit_reason": "done",
            "index": 1,
            "prompt": "second",
            "tokens": 0,
            "tool_calls": 0,
        },
    ]
    assert [call["model"] for call in calls] == ["test/model", "test/model"]
    assert [call["max_turns"] for call in calls] == [3, 3]
    assert [call["timeout"] for call in calls] == [7.0, 7.0]


def test_batch_empty_input_outputs_no_records(monkeypatch):
    monkeypatch.setattr(
        cmd_batch,
        "_run_one_prompt",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not run")),
    )

    runner = CliRunner()
    result = runner.invoke(util_main, ["batch"], input="\n \n")

    assert result.exit_code == 0, result.output
    assert result.output == ""


def test_summarize_child_output_counts_tokens_and_max_turns():
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": "hello",
                    "metadata": {
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "cache_read_tokens": 2,
                            "cache_creation_tokens": 1,
                        }
                    },
                }
            ),
            json.dumps(
                {
                    "type": "system",
                    "content": "ignored content",
                    "metadata": {"usage": {"input_tokens": 3}},
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "role": "system",
                    "content": "Stopped: reached max steps limit (1)",
                }
            ),
        ]
    )

    record = cmd_batch._summarize_child_output(
        index=4,
        prompt="do it",
        duration_s=1.23456,
        returncode=0,
        stdout=stdout,
        stderr="",
    )

    assert record == {
        "duration_s": 1.235,
        "exit_reason": "max_turns",
        "index": 4,
        "prompt": "do it",
        "tokens": 21,
        "tool_calls": 0,
    }


def test_summarize_child_output_reports_error_tail():
    record = cmd_batch._summarize_child_output(
        index=0,
        prompt="bad",
        duration_s=0.5,
        returncode=2,
        stdout="not json\n",
        stderr="first line\nlast line\n",
    )

    assert record["exit_reason"] == "error"
    assert record["returncode"] == 2
    assert record["error"] == "last line"


def test_usage_tokens_handles_missing_usage():
    """Usage key=None should return 0 tokens."""
    assert cmd_batch._usage_tokens({"usage": None}) == 0  # type: ignore[arg-type]
    assert cmd_batch._usage_tokens({}) == 0


def test_usage_tokens_handles_non_int_values():
    """Non-int token values should be skipped."""
    record = {"usage": {"input_tokens": "10", "output_tokens": 5}}  # type: ignore[dict-item]
    assert cmd_batch._usage_tokens(record) == 5


def test_iter_json_events_skips_non_dict_and_invalid():
    lines = [
        json.dumps({"type": "message"}),
        "not json at all",
        json.dumps("just a string, not a dict"),
        "",
        json.dumps(["a list"]),
        json.dumps({"type": "system"}),
    ]
    events = list(cmd_batch._iter_json_events("\n".join(lines)))
    assert len(events) == 2
    assert events[0]["type"] == "message"
    assert events[1]["type"] == "system"


def test_summarize_child_output_error_no_stderr():
    """Non-zero exit but no stderr — still reports error, no error key."""
    record = cmd_batch._summarize_child_output(
        index=0,
        prompt="crash",
        duration_s=1.0,
        returncode=1,
        stdout="",
        stderr="",
    )
    assert record["exit_reason"] == "error"
    assert record["returncode"] == 1
    assert "error" not in record


def test_summarize_child_output_counts_tool_calls():
    """_count_tool_calls should detect ToolUse blocks in content."""
    # Simulate a message that contains tool-use markers
    content_with_tools = (
        "Let me check that.\n\n"
        "```shell\nls\n```\n\n"
        "Now I'll save.\n\n"
        "```save test.txt\nhello\n```"
    )
    count = cmd_batch._count_tool_calls(content_with_tools)
    # ToolUse.iter_from_content parses fenced code blocks as tool calls
    assert count == 2


def test_run_one_prompt_invokes_child_process(monkeypatch):
    calls = []
    times = iter([10.0, 12.25])

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        stdout = json.dumps({"type": "message", "content": "done"}) + "\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(cmd_batch.subprocess, "run", fake_run)
    monkeypatch.setattr(cmd_batch.sys, "executable", "/usr/bin/python-test")
    monkeypatch.setattr(cmd_batch.time, "monotonic", lambda: next(times))

    record = cmd_batch._run_one_prompt(
        index=2,
        prompt="say hello",
        model="test/model",
        max_turns=4,
        timeout=9.5,
    )

    assert record == {
        "duration_s": 2.25,
        "exit_reason": "done",
        "index": 2,
        "prompt": "say hello",
        "tokens": 0,
        "tool_calls": 0,
    }
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd == [
        "/usr/bin/python-test",
        "-m",
        "gptme",
        "--non-interactive",
        "--output-format",
        "json",
        "--no-stream",
        "--model",
        "test/model",
        "say hello",
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 9.5
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["check"] is False
    assert kwargs["env"]["GPTME_MAX_STEPS"] == "4"


def test_run_one_prompt_reports_timeout(monkeypatch):
    times = iter([1.0, 4.4567])

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(cmd_batch.subprocess, "run", fake_run)
    monkeypatch.setattr(cmd_batch.time, "monotonic", lambda: next(times))

    record = cmd_batch._run_one_prompt(
        index=1,
        prompt="slow",
        model=None,
        max_turns=2,
        timeout=2.5,
    )

    assert record == {
        "duration_s": 3.457,
        "error": "timed out after 2.5s",
        "exit_reason": "timeout",
        "index": 1,
        "prompt": "slow",
        "returncode": None,
        "tokens": 0,
        "tool_calls": 0,
    }


def test_batch_without_jsonl_only_outputs_progress(monkeypatch):
    """--jsonl-only off (default) emits stderr progress."""

    def fake_run_one_prompt(**kwargs):
        return {
            "index": kwargs["index"],
            "prompt": kwargs["prompt"],
            "exit_reason": "done",
            "tokens": 0,
            "duration_s": 0.5,
            "tool_calls": 0,
        }

    monkeypatch.setattr(cmd_batch, "_run_one_prompt", fake_run_one_prompt)

    runner = CliRunner()
    result = runner.invoke(
        util_main, ["batch", "--model", "test/model"], input="hello\n"
    )

    assert result.exit_code == 0, result.output
    progress_output = (
        result.stderr if result.stderr_bytes is not None else result.output
    )
    assert "[1/1]" in progress_output
    assert "done" in progress_output
    assert "0.5s" in progress_output
