from pathlib import Path
from unittest.mock import patch

from gptme.message import Message
from gptme.tools.pruner import plan_tool_output_prune


def test_plan_tool_output_prune_selects_requested_ranges(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GPTME_PRUNE_TOOL_OUTPUT", "1")
    monkeypatch.setenv("GPTME_PRUNE_TOOL_OUTPUT_THRESHOLD_TOKENS", "1")
    monkeypatch.setenv("GPTME_PRUNE_TOOL_OUTPUT_MODEL", "mock/echo")

    output = "keep 1\ndrop 2\nkeep 3\ndrop 4\n"

    with (
        patch("gptme.tools.pruner._latest_user_query", return_value="keep lines"),
        patch("gptme.tools.pruner._resolve_model_name", return_value="mock/echo"),
        patch(
            "gptme.tools.pruner._estimate_tokens",
            side_effect=lambda text, model: max(1, len(text.splitlines())),
        ),
        patch("gptme.tools.pruner._threshold_tokens", return_value=1),
        patch(
            "gptme.llm.reply",
            return_value=Message("assistant", '{"ranges": [[1, 1], [3, 3]]}'),
        ),
    ):
        plan = plan_tool_output_prune("shell", output, context_label="rg keep .")

    assert plan is not None
    assert plan.ranges == ((1, 1), (3, 3))
    assert plan.kept_lines == 2
    assert plan.apply(output.splitlines()) == "keep 1\nkeep 3"


def test_plan_tool_output_prune_skips_full_output_requests(monkeypatch):
    monkeypatch.setenv("GPTME_PRUNE_TOOL_OUTPUT", "1")
    monkeypatch.setenv("GPTME_PRUNE_TOOL_OUTPUT_THRESHOLD_TOKENS", "1")

    with patch(
        "gptme.tools.pruner._latest_user_query",
        return_value="show the full output",
    ):
        assert plan_tool_output_prune("shell", "line 1\nline 2\n") is None
