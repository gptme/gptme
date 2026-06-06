"""Tests for gptme-util batch command."""

from __future__ import annotations

import json

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
        "tokens": 18,
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
