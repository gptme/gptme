"""Tests for the gptme-eval tool-efficiency (tool-call count) metric."""

import csv
from pathlib import Path
from typing import Literal

from gptme.eval.main import (
    print_model_results_table,
    read_results_from_csv,
    write_results,
)
from gptme.eval.run import count_tool_calls
from gptme.eval.types import EvalResult, ModelConfig
from gptme.message import Message
from gptme.tools import init_tools


def test_count_tool_calls_counts_runnable_assistant_tooluses():
    """count_tool_calls counts runnable tool-uses in assistant messages only."""
    init_tools()

    messages = [
        Message("system", "You are a helpful assistant."),
        Message("user", "List the files and then read main.py."),
        # assistant turn with one runnable shell tool-use
        Message("assistant", "Sure, listing files:\n\n```shell\nls -la\n```"),
        # tool output is a system message — not an assistant turn, not counted
        Message("system", "```stdout\nmain.py\n```"),
        # assistant turn with another runnable tool-use
        Message("assistant", "Now reading it:\n\n```shell\ncat main.py\n```"),
        # assistant turn with prose only — no tool-use
        Message("assistant", "The file defines a single function."),
    ]

    assert count_tool_calls(messages) == 2


def test_count_tool_calls_empty():
    """No messages (e.g. a log that failed to load) yields zero."""
    assert count_tool_calls([]) == 0


def _make_result(
    tool_calls: int | None = None,
    *,
    status: Literal["success", "error", "timeout"] = "success",
) -> EvalResult:
    kwargs: dict = {}
    if tool_calls is not None:
        kwargs["tool_calls"] = tool_calls
    return EvalResult(
        name="hello",
        status=status,
        results=[],
        timings={"gen": 1.0, "run": 0.5, "eval": 0.1},
        gen_stdout="",
        gen_stderr="",
        run_stdout="",
        run_stderr="",
        log_dir=Path("/tmp/log"),
        workspace_dir=Path("/tmp/ws"),
        **kwargs,
    )


def test_evalresult_to_dict_includes_tool_calls():
    """EvalResult serializes the tool_calls field; default is 0."""
    assert _make_result().to_dict()["tool_calls"] == 0
    assert _make_result(tool_calls=4).to_dict()["tool_calls"] == 4


def test_write_results_persists_tool_calls_roundtrip(tmp_path, monkeypatch):
    """CSV persistence should preserve tool_calls for replayed results."""
    monkeypatch.setenv("EVAL_RESULTS_DIR", str(tmp_path))

    config = ModelConfig(model="test-model", tool_format="tool")
    write_results({config: [_make_result(tool_calls=4)]})

    csv_path = next(tmp_path.glob("*/eval_results.csv"))
    with open(csv_path, newline="") as f:
        row = next(csv.DictReader(f))
    assert row["Tool Calls"] == "4"

    model_results = read_results_from_csv(str(csv_path))
    assert model_results[config][0].tool_calls == 4


def test_print_model_results_table_hides_tool_calls_for_error_rows(capsys):
    """Error rows should show the status marker instead of a fake 0tc metric."""
    config = ModelConfig(model="test-model", tool_format="tool")
    print_model_results_table({config: [_make_result(tool_calls=4, status="error")]})

    output = capsys.readouterr().out
    assert "error" in output
    assert "/4tc" not in output
