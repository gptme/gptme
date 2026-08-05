"""Tests for global LLM usage and cost analytics CLI command (`gptme stats`)."""

import json
import time
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from gptme.cli.cmd_stats import gather_global_stats, stats


def _create_mock_conversation(
    conv_dir: Path,
    conv_id: str,
    model: str,
    cost: float,
    input_tokens: int,
    output_tokens: int,
    modified_offset_seconds: float = 0,
) -> Path:
    """Helper to write a mock conversation.jsonl file."""
    log_dir = conv_dir / conv_id
    log_dir.mkdir(parents=True, exist_ok=True)
    conv_file = log_dir / "conversation.jsonl"

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Hello agent"},
        {
            "role": "assistant",
            "content": "Hello user",
            "metadata": {
                "model": model,
                "cost": cost,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            },
        },
    ]

    with open(conv_file, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(msg) + "\n" for msg in messages)

    if modified_offset_seconds != 0:
        mtime = time.time() + modified_offset_seconds
        import os

        os.utime(conv_file, (mtime, mtime))

    return conv_file


def test_gather_global_stats_empty(tmp_path: Path, monkeypatch: Any) -> None:
    """Test gathering stats when no conversation logs exist."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    summary = gather_global_stats()
    assert summary.total_sessions == 0
    assert summary.total_cost == 0.0
    assert summary.total_tokens == 0
    assert summary.avg_cost_per_session == 0.0
    assert summary.by_model == []


def test_gather_global_stats_aggregation(tmp_path: Path, monkeypatch: Any) -> None:
    """Test gathering stats across multiple conversation logs with different models."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    # Conversation 1: OpenAI
    _create_mock_conversation(
        tmp_path,
        conv_id="2026-07-01-test-openai",
        model="openai/gpt-4o",
        cost=0.05,
        input_tokens=1000,
        output_tokens=200,
    )

    # Conversation 2: Anthropic
    _create_mock_conversation(
        tmp_path,
        conv_id="2026-07-02-test-anthropic",
        model="anthropic/claude-3-5-sonnet",
        cost=0.10,
        input_tokens=2000,
        output_tokens=500,
    )

    # Conversation 3: Second OpenAI session
    _create_mock_conversation(
        tmp_path,
        conv_id="2026-07-03-test-openai-2",
        model="openai/gpt-4o",
        cost=0.02,
        input_tokens=400,
        output_tokens=100,
    )

    summary = gather_global_stats()
    assert summary.total_sessions == 3
    assert abs(summary.total_cost - 0.17) < 1e-6
    assert summary.total_input_tokens == 3400
    assert summary.total_output_tokens == 800
    assert summary.total_tokens == 4200

    # Model breakdown assertions
    assert len(summary.by_model) == 2

    # Anthropic should be first (highest cost = 0.10)
    anthropic_stats = summary.by_model[0]
    assert anthropic_stats.model == "anthropic/claude-3-5-sonnet"
    assert anthropic_stats.sessions == 1
    assert abs(anthropic_stats.cost - 0.10) < 1e-6

    # OpenAI should be second (total cost = 0.07 across 2 sessions)
    openai_stats = summary.by_model[1]
    assert openai_stats.model == "openai/gpt-4o"
    assert openai_stats.sessions == 2
    assert abs(openai_stats.cost - 0.07) < 1e-6


def test_gather_global_stats_time_filtering(tmp_path: Path, monkeypatch: Any) -> None:
    """Test filtering stats by N days."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    # Recent session (today)
    _create_mock_conversation(
        tmp_path,
        conv_id="recent-session",
        model="openai/gpt-4o",
        cost=0.05,
        input_tokens=1000,
        output_tokens=200,
        modified_offset_seconds=0,
    )

    # Old session (10 days ago)
    _create_mock_conversation(
        tmp_path,
        conv_id="old-session",
        model="openai/gpt-4o",
        cost=0.50,
        input_tokens=10000,
        output_tokens=2000,
        modified_offset_seconds=-(10 * 86400 + 3600),
    )

    # All-time stats
    all_summary = gather_global_stats(days=None)
    assert all_summary.total_sessions == 2

    # Stats for last 7 days (should exclude the 10-day old session)
    recent_summary = gather_global_stats(days=7)
    assert recent_summary.total_sessions == 1
    assert abs(recent_summary.total_cost - 0.05) < 1e-6


def test_cli_stats_json_output(tmp_path: Path, monkeypatch: Any) -> None:
    """Test CLI output in JSON format."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    _create_mock_conversation(
        tmp_path,
        conv_id="cli-json-session",
        model="openai/gpt-4o",
        cost=0.03,
        input_tokens=600,
        output_tokens=150,
    )

    runner = CliRunner()
    result = runner.invoke(stats, ["--json"])
    assert result.exit_code == 0

    data = json.loads(result.output)
    assert data["total_sessions"] == 1
    assert data["total_cost"] == 0.03
    assert data["total_tokens"]["input"] == 600
    assert data["total_tokens"]["output"] == 150
    assert len(data["by_model"]) == 1
    assert data["by_model"][0]["model"] == "openai/gpt-4o"


def test_cli_stats_human_readable_output(tmp_path: Path, monkeypatch: Any) -> None:
    """Test CLI output in human-readable table format."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    _create_mock_conversation(
        tmp_path,
        conv_id="cli-table-session",
        model="anthropic/claude-3-5-sonnet",
        cost=0.08,
        input_tokens=1500,
        output_tokens=300,
    )

    runner = CliRunner()
    result = runner.invoke(stats, [])
    assert result.exit_code == 0
    assert "gptme Usage & Cost Analytics" in result.output
    assert "Total Sessions" in result.output
    assert "anthropic/claude-3-5-sonnet" in result.output


def test_format_cost_edge_cases() -> None:
    """Test cost formatting edge cases ($0.0000 and <$0.0001)."""
    from gptme.cli.cmd_stats import _format_cost

    assert _format_cost(0.0) == "$0.0000"
    assert _format_cost(0.00005) == "<$0.0001"


def test_display_stats_empty(capsys: Any) -> None:
    """Test display_stats when no conversation logs exist."""
    from gptme.cli.cmd_stats import StatsSummary, display_stats

    summary = StatsSummary()
    display_stats(summary)
    captured = capsys.readouterr()
    assert "No conversation logs found" in captured.out


def test_gather_global_stats_mixed_model_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Test exact per-message model attribution for cost and tokens in multi-model sessions."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))

    log_dir = tmp_path / "mixed-model-session"
    log_dir.mkdir(parents=True, exist_ok=True)
    conv_file = log_dir / "conversation.jsonl"

    messages = [
        {"role": "user", "content": "Question 1"},
        {
            "role": "assistant",
            "content": "Answer 1",
            "metadata": {
                "model": "openai/gpt-4o",
                "cost": 0.04,
                "usage": {"input_tokens": 400, "output_tokens": 100},
            },
        },
        {"role": "user", "content": "Question 2"},
        {
            "role": "assistant",
            "content": "Answer 2",
            "metadata": {
                "model": "anthropic/claude-3-5-sonnet",
                "cost": 0.10,
                "usage": {"input_tokens": 1000, "output_tokens": 200},
            },
        },
    ]

    with open(conv_file, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(msg) + "\n" for msg in messages)

    summary = gather_global_stats()
    assert summary.total_sessions == 1
    assert abs(summary.total_cost - 0.14) < 1e-6

    by_model_map = {m.model: m for m in summary.by_model}
    assert "openai/gpt-4o" in by_model_map
    assert "anthropic/claude-3-5-sonnet" in by_model_map

    gpt4o = by_model_map["openai/gpt-4o"]
    claude = by_model_map["anthropic/claude-3-5-sonnet"]

    assert abs(gpt4o.cost - 0.04) < 1e-6
    assert gpt4o.input_tokens == 400
    assert gpt4o.output_tokens == 100

    assert abs(claude.cost - 0.10) < 1e-6
    assert claude.input_tokens == 1000
    assert claude.output_tokens == 200


def test_display_stats_marks_cache_as_subset_of_input():
    """Cache-read tokens are counted inside input tokens, so the display must
    say so explicitly rather than listing `cache` as a third additive term."""
    from rich.console import Console

    from gptme.cli.cmd_stats import StatsSummary, display_stats

    summary = StatsSummary(
        total_sessions=1,
        total_cost=0.5,
        total_input_tokens=150,
        total_output_tokens=50,
        total_cache_read_tokens=50,
    )
    # total is in + out only; cache is already inside in
    assert summary.total_tokens == 200

    console = Console(record=True, width=200, no_color=True)
    display_stats(summary, console=console)
    output = console.export_text()

    # cache figure is nested inside the input term, marked as included
    assert "incl. 50 cached" in output
    # and is not presented as a separate additive term
    assert "/ cache: 50" not in output


def test_stats_json_documents_cache_subset_relationship():
    """JSON output keeps cache_read alongside input; the two must not be
    expected to sum to total."""
    from gptme.cli.cmd_stats import StatsSummary

    summary = StatsSummary(
        total_sessions=1,
        total_cost=0.5,
        total_input_tokens=150,
        total_output_tokens=50,
        total_cache_read_tokens=50,
    )
    d = summary.to_dict()
    tokens = d["total_tokens"]
    assert tokens["total"] == tokens["input"] + tokens["output"]
    assert tokens["cache_read"] <= tokens["input"]
