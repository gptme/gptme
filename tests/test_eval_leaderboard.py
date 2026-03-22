"""Tests for the eval leaderboard aggregation script."""

import csv

# Add scripts to path so we can import the module
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from eval_leaderboard import (
    aggregate_results,
    format_csv_table,
    format_markdown_table,
    format_rst_table,
    load_results,
    normalize_model,
    parse_model_format,
)


def test_parse_model_format_with_at():
    """Model names with @format should be split."""
    model, fmt = parse_model_format("anthropic/claude-3-5-haiku@markdown", "")
    assert model == "anthropic/claude-3-5-haiku"
    assert fmt == "markdown"


def test_parse_model_format_without_at():
    """Model names without @format use the format column."""
    model, fmt = parse_model_format("anthropic/claude-3-5-haiku", "tool")
    assert model == "anthropic/claude-3-5-haiku"
    assert fmt == "tool"


def test_parse_model_format_empty():
    """Model with no format info."""
    model, fmt = parse_model_format("openai/gpt-4o", "")
    assert model == "openai/gpt-4o"
    assert fmt == ""


def test_normalize_model():
    """Known models get human-readable names."""
    assert normalize_model("openai/gpt-4o") == "GPT-4o"
    assert normalize_model("anthropic/claude-sonnet-4-20250514") == "Claude Sonnet 4"
    # Unknown models pass through unchanged
    assert normalize_model("some/unknown-model") == "some/unknown-model"


def _create_eval_results(tmp_path: Path, runs: list[dict]) -> Path:
    """Helper to create eval results directory structure."""
    for run in runs:
        run_dir = tmp_path / run["dir"]
        run_dir.mkdir(parents=True, exist_ok=True)
        csv_path = run_dir / "eval_results.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "Model",
                    "Tool Format",
                    "Test",
                    "Passed",
                    "Total Duration",
                    "Generation Time",
                    "Run Time",
                    "Eval Time",
                    "Commit Hash",
                    "Log Dir",
                    "Workspace Dir",
                ],
            )
            writer.writeheader()
            for row in run["rows"]:
                writer.writerow(
                    {
                        "Model": row[0],
                        "Tool Format": row[1],
                        "Test": row[2],
                        "Passed": row[3],
                        "Total Duration": "10.0",
                        "Generation Time": "10.0",
                        "Run Time": "0.0",
                        "Eval Time": "0.0",
                        "Commit Hash": "abc123",
                        "Log Dir": "/tmp/logs",
                        "Workspace Dir": "/tmp/ws",
                    }
                )
    return tmp_path


def test_load_results(tmp_path):
    """Loading results from CSV files."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "markdown", "hello", "true"),
                    ("openai/gpt-4o", "markdown", "prime100", "false"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    assert len(results) == 2
    assert results[0]["model"] == "openai/gpt-4o"
    assert results[0]["passed"] is True
    assert results[1]["passed"] is False


def test_load_results_model_at_format(tmp_path):
    """Loading results with @format in model name."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o@tool", "", "hello", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    assert len(results) == 1
    assert results[0]["model"] == "openai/gpt-4o"
    assert results[0]["format"] == "tool"


def test_aggregate_best_format(tmp_path):
    """Aggregation picks the best format per model."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    # markdown format: 4/5 pass
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "true"),
                    ("model-a", "markdown", "hello-patch", "true"),
                    ("model-a", "markdown", "hello-ask", "true"),
                    ("model-a", "markdown", "fix-bug", "false"),
                    # tool format: 2/5 pass
                    ("model-a", "tool", "hello", "true"),
                    ("model-a", "tool", "prime100", "false"),
                    ("model-a", "tool", "hello-patch", "true"),
                    ("model-a", "tool", "hello-ask", "false"),
                    ("model-a", "tool", "fix-bug", "false"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    assert len(ranked) == 1
    assert ranked[0]["format"] == "markdown"
    assert ranked[0]["pass_rate"] == pytest.approx(0.8)


def test_aggregate_latest_result_wins(tmp_path):
    """Later runs override earlier runs for the same test."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1_early",
                "rows": [
                    ("model-a", "markdown", "hello", "false"),
                    ("model-a", "markdown", "prime100", "false"),
                    ("model-a", "markdown", "hello-patch", "false"),
                    ("model-a", "markdown", "hello-ask", "false"),
                    ("model-a", "markdown", "fix-bug", "false"),
                ],
            },
            {
                "dir": "run2_later",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "true"),
                    ("model-a", "markdown", "hello-patch", "true"),
                    ("model-a", "markdown", "hello-ask", "true"),
                    ("model-a", "markdown", "fix-bug", "true"),
                ],
            },
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    assert ranked[0]["pass_rate"] == 1.0


def test_aggregate_min_tests_filter(tmp_path):
    """Models with too few tests are excluded."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=5)
    assert len(ranked) == 0
    ranked = aggregate_results(results, min_tests=2)
    assert len(ranked) == 1


def test_format_rst_table(tmp_path):
    """RST table output format."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "markdown", "hello", "true"),
                    ("openai/gpt-4o", "markdown", "prime100", "true"),
                    ("openai/gpt-4o", "markdown", "hello-patch", "false"),
                    ("openai/gpt-4o", "markdown", "hello-ask", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    table = format_rst_table(ranked)
    assert "GPT-4o" in table
    assert "markdown" in table
    assert "3/4" in table


def test_format_markdown_table(tmp_path):
    """Markdown table output format."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "markdown", "hello", "true"),
                    ("openai/gpt-4o", "markdown", "prime100", "true"),
                    ("openai/gpt-4o", "markdown", "hello-patch", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    table = format_markdown_table(ranked)
    assert "| GPT-4o |" in table
    assert "| Model |" in table


def test_empty_results_dir(tmp_path):
    """Empty results directory returns empty list."""
    results = load_results(tmp_path)
    assert results == []


def test_load_results_missing_dir(tmp_path):
    """Missing results directory returns empty list (no crash)."""
    missing = tmp_path / "does_not_exist"
    results = load_results(missing)
    assert results == []


def test_format_rst_table_wide_overall(tmp_path):
    """RST table Overall column must not overflow for models with ≥10 tests."""
    rows = [
        ("openai/gpt-4o", "markdown", f"test-{i}", "true" if i % 3 != 0 else "false")
        for i in range(15)
    ]
    _create_eval_results(tmp_path, [{"dir": "run1", "rows": rows}])
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=5)
    table = format_rst_table(ranked)

    # Verify every data row fits within the separator width
    lines = table.splitlines()
    sep_len = len(lines[0])
    for line in lines[3:-1]:  # skip header sep, header, second sep, footer sep
        assert len(line) <= sep_len, f"Row too wide: {line!r}"


def test_format_csv_table(tmp_path):
    """CSV output format produces valid CSV with expected columns."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "markdown", "hello", "true"),
                    ("openai/gpt-4o", "markdown", "prime100", "true"),
                    ("openai/gpt-4o", "markdown", "hello-patch", "false"),
                    ("openai/gpt-4o", "markdown", "hello-ask", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    output = format_csv_table(ranked)
    rows = list(csv.reader(output.splitlines()))
    assert rows[0] == [
        "Model",
        "Format",
        "Passed",
        "Total",
        "Pass Rate",
        "Basic",
        "Practical",
    ]
    assert rows[1][0] == "GPT-4o"
    assert rows[1][1] == "markdown"
    assert rows[1][2] == "3"  # passed
    assert rows[1][3] == "4"  # total


def test_format_rst_table_unknown_model_no_overflow(tmp_path):
    """RST table adapts column width for unknown models with long API paths."""
    long_model = "openrouter/provider/a-very-long-model-name-that-exceeds-35-chars"
    rows = [(long_model, "tool", f"test-{i}", "true") for i in range(5)]
    _create_eval_results(tmp_path, [{"dir": "run1", "rows": rows}])
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=5)
    table = format_rst_table(ranked)
    assert long_model in table  # full name not truncated
    # Verify every data row fits within the separator width
    lines = table.splitlines()
    sep_len = len(lines[0])
    for line in lines[3:-1]:
        assert len(line) <= sep_len, f"Row too wide: {line!r}"


def test_aggregate_tiebreak_by_total_tests(tmp_path):
    """On equal pass rates, prefer the format/model with more tests."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    # model-a: markdown 2/2, tool 4/4 — equal 100% but tool has more tests
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "true"),
                    ("model-a", "tool", "hello", "true"),
                    ("model-a", "tool", "prime100", "true"),
                    ("model-a", "tool", "hello-patch", "true"),
                    ("model-a", "tool", "hello-ask", "true"),
                    # model-b: 3/4 (75%) — lower pass rate
                    ("model-b", "markdown", "hello", "true"),
                    ("model-b", "markdown", "prime100", "true"),
                    ("model-b", "markdown", "hello-patch", "true"),
                    ("model-b", "markdown", "hello-ask", "false"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=2)
    # model-a should be first (higher pass rate)
    assert ranked[0]["model"] == "model-a"
    # tool format selected for model-a because it has more tests (4 > 2) at equal pass rate
    assert ranked[0]["format"] == "tool"
    assert ranked[0]["total_tests"] == 4
    # model-b is second
    assert ranked[1]["model"] == "model-b"


def test_suite_classification(tmp_path):
    """Tests are correctly classified into basic and practical suites."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    # Basic tests
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "true"),
                    ("model-a", "markdown", "fix-bug", "true"),
                    # Practical tests
                    ("model-a", "markdown", "build-api", "true"),
                    ("model-a", "markdown", "rename-function", "false"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    assert len(ranked) == 1
    assert ranked[0]["basic_passed"] == 3
    assert ranked[0]["basic_total"] == 3
    assert ranked[0]["practical_passed"] == 1
    assert ranked[0]["practical_total"] == 2
