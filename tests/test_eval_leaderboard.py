"""Tests for the eval leaderboard aggregation module."""

import csv
import json
from pathlib import Path

import pytest

from gptme.eval.leaderboard import (
    aggregate_per_test,
    aggregate_results,
    format_csv_table,
    format_html_page,
    format_json,
    format_markdown_table,
    format_per_test_html,
    format_per_test_markdown,
    format_rst_table,
    generate_leaderboard,
    load_results,
    main,
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
    assert normalize_model("anthropic/claude-sonnet-4-5") == "Claude Sonnet 4.5"
    assert normalize_model("openrouter/openai/gpt-4o-mini") == "GPT-4o Mini (OR)"
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


def test_format_json(tmp_path):
    """JSON output format produces valid JSON with expected structure."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "markdown", "hello", "true"),
                    ("openai/gpt-4o", "markdown", "prime100", "true"),
                    ("openai/gpt-4o", "markdown", "build-api", "false"),
                    ("openai/gpt-4o", "markdown", "hello-patch", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    output = format_json(ranked)
    data = json.loads(output)
    assert "models" in data
    assert len(data["models"]) == 1
    model = data["models"][0]
    assert model["model"] == "openai/gpt-4o"
    assert model["display_name"] == "GPT-4o"
    assert model["format"] == "markdown"
    assert model["pass_rate"] == 0.75
    assert model["total_passed"] == 3
    assert model["total_tests"] == 4
    assert model["basic"]["passed"] == 3
    assert model["basic"]["total"] == 3
    assert model["practical"]["passed"] == 0
    assert model["practical"]["total"] == 1


def test_generate_leaderboard_markdown(tmp_path):
    """generate_leaderboard() produces markdown output."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "tool", "hello", "true"),
                    ("openai/gpt-4o", "tool", "prime100", "true"),
                    ("openai/gpt-4o", "tool", "fix-bug", "true"),
                    ("openai/gpt-4o", "tool", "hello-patch", "false"),
                ],
            }
        ],
    )
    output = generate_leaderboard(
        results_dir=tmp_path,
        output_format="markdown",
        min_tests=3,
    )
    assert "| GPT-4o |" in output
    assert "3/4" in output


def test_generate_leaderboard_json(tmp_path):
    """generate_leaderboard() produces valid JSON output."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "tool", "hello", "true"),
                    ("openai/gpt-4o", "tool", "prime100", "true"),
                    ("openai/gpt-4o", "tool", "fix-bug", "false"),
                    ("openai/gpt-4o", "tool", "hello-patch", "true"),
                ],
            }
        ],
    )
    output = generate_leaderboard(
        results_dir=tmp_path,
        output_format="json",
        min_tests=3,
    )
    data = json.loads(output)
    assert data["models"][0]["pass_rate"] == 0.75


def test_generate_leaderboard_invalid_format(tmp_path):
    """generate_leaderboard() raises ValueError for unknown format strings."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "tool", "hello", "true"),
                    ("openai/gpt-4o", "tool", "prime100", "true"),
                    ("openai/gpt-4o", "tool", "fix-bug", "false"),
                    ("openai/gpt-4o", "tool", "hello-patch", "true"),
                ],
            }
        ],
    )
    with pytest.raises(ValueError, match="Unknown format"):
        generate_leaderboard(
            results_dir=tmp_path,
            output_format="xml",
            min_tests=3,
        )


def test_format_html_page(tmp_path):
    """HTML output produces a self-contained page with expected structure."""
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
                    ("anthropic/claude-sonnet-4-20250514", "tool", "hello", "true"),
                    ("anthropic/claude-sonnet-4-20250514", "tool", "prime100", "true"),
                    (
                        "anthropic/claude-sonnet-4-20250514",
                        "tool",
                        "hello-patch",
                        "true",
                    ),
                    ("anthropic/claude-sonnet-4-20250514", "tool", "hello-ask", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    html = format_html_page(ranked)
    # Valid HTML structure
    assert "<!DOCTYPE html>" in html
    assert "<title>gptme Eval Leaderboard</title>" in html
    assert "</html>" in html
    # Contains model names (normalized)
    assert "GPT-4o" in html
    assert "Claude Sonnet 4" in html
    # Contains pass rate badges
    assert "badge-green" in html
    # Contains ranking numbers
    assert "<td class='rank'>1</td>" in html
    assert "<td class='rank'>2</td>" in html
    # Claude should be #1 (100% > 75%)
    assert html.index("Claude Sonnet 4") < html.index("GPT-4o")


def test_format_html_escapes_model_names(tmp_path):
    """HTML output escapes special characters in model names."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("some/<script>evil</script>", "tool", f"test-{i}", "true")
                    for i in range(5)
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    ranked = aggregate_results(results, min_tests=3)
    html = format_html_page(ranked)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_main_graceful_on_missing_results(tmp_path, capsys, monkeypatch):
    """CLI main() prints placeholder instead of crashing when no results exist."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "leaderboard",
            "--results-dir",
            str(tmp_path / "nonexistent"),
            "--format",
            "rst",
        ],
    )
    # main() should not raise or sys.exit
    main()
    captured = capsys.readouterr()
    assert "No eval results available" in captured.out


def test_generate_leaderboard_html(tmp_path):
    """generate_leaderboard() with html format produces valid HTML."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "tool", "hello", "true"),
                    ("openai/gpt-4o", "tool", "prime100", "true"),
                    ("openai/gpt-4o", "tool", "fix-bug", "true"),
                    ("openai/gpt-4o", "tool", "hello-patch", "false"),
                ],
            }
        ],
    )
    output = generate_leaderboard(
        results_dir=tmp_path,
        output_format="html",
        min_tests=3,
    )
    assert "<!DOCTYPE html>" in output
    assert "GPT-4o" in output
    assert "gptme Eval Leaderboard" in output


def test_normalize_model_openrouter_proxied():
    """OpenRouter-proxied models get display names with (OR) suffix."""
    assert (
        normalize_model("openrouter/anthropic/claude-sonnet-4-6")
        == "Claude Sonnet 4.6 (OR)"
    )
    assert normalize_model("openrouter/openai/gpt-4o") == "GPT-4o (OR)"


def test_aggregate_per_test(tmp_path):
    """Per-test aggregation builds a correct model x test matrix."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("model-a", "tool", "hello", "true"),
                    ("model-a", "tool", "prime100", "false"),
                    ("model-a", "tool", "build-api", "true"),
                    ("model-b", "tool", "hello", "true"),
                    ("model-b", "tool", "prime100", "true"),
                    ("model-b", "tool", "build-api", "false"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    model_names, test_names, matrix = aggregate_per_test(results, min_tests=2)

    # Both models should appear
    assert len(model_names) == 2
    # model-b has higher pass rate (2/3 vs 2/3 — tie, alphabetical)
    # Actually both are 2/3, order may vary

    # Test names should be ordered: basic first, then practical
    hello_idx = test_names.index("hello")
    prime_idx = test_names.index("prime100")
    api_idx = test_names.index("build-api")
    assert hello_idx < api_idx  # basic before practical
    assert prime_idx < api_idx

    # Matrix values
    assert matrix["model-a"]["hello"] is True
    assert matrix["model-a"]["prime100"] is False
    assert matrix["model-a"]["build-api"] is True
    assert matrix["model-b"]["hello"] is True


def test_aggregate_per_test_min_tests_filter(tmp_path):
    """Per-test aggregation respects min_tests threshold."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("model-a", "tool", "hello", "true"),
                    ("model-b", "tool", "hello", "true"),
                    ("model-b", "tool", "prime100", "true"),
                    ("model-b", "tool", "fix-bug", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    model_names, _, _ = aggregate_per_test(results, min_tests=3)
    # model-a has only 1 test, should be excluded
    assert "model-a" not in model_names
    assert "model-b" in model_names


def test_format_per_test_markdown(tmp_path):
    """Per-test markdown output has correct structure."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "tool", "hello", "true"),
                    ("openai/gpt-4o", "tool", "prime100", "false"),
                    ("openai/gpt-4o", "tool", "build-api", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    model_names, test_names, matrix = aggregate_per_test(results, min_tests=2)
    output = format_per_test_markdown(model_names, test_names, matrix)

    assert "GPT-4o" in output  # normalized name
    assert "| hello |" in output
    assert "| P |" in output  # passed
    assert "| F |" in output  # failed


def test_format_per_test_html(tmp_path):
    """Per-test HTML output has correct structure."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "tool", "hello", "true"),
                    ("openai/gpt-4o", "tool", "prime100", "false"),
                    ("openai/gpt-4o", "tool", "build-api", "true"),
                ],
            }
        ],
    )
    results = load_results(tmp_path)
    model_names, test_names, matrix = aggregate_per_test(results, min_tests=2)
    output = format_per_test_html(model_names, test_names, matrix)

    assert "<!DOCTYPE html>" in output
    assert "Per-Test Breakdown" in output
    assert "GPT-4o" in output
    assert "class='pass'" in output
    assert "class='fail'" in output
    # Suite headers present
    assert "Basic" in output
    assert "Practical" in output


def test_main_per_test_flag(tmp_path, capsys, monkeypatch):
    """CLI --per-test flag produces per-test output."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "run1",
                "rows": [
                    ("openai/gpt-4o", "tool", "hello", "true"),
                    ("openai/gpt-4o", "tool", "prime100", "true"),
                    ("openai/gpt-4o", "tool", "fix-bug", "false"),
                    ("openai/gpt-4o", "tool", "hello-patch", "true"),
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "leaderboard",
            "--results-dir",
            str(tmp_path),
            "--per-test",
            "--min-tests",
            "3",
        ],
    )
    main()
    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert "GPT-4o" in captured.out
    assert "| P |" in captured.out


def test_practical_tests_sync():
    """PRACTICAL_TESTS set stays in sync with actual practical suite definitions.

    If this test fails, a new practical suite was added but its test names
    weren't added to PRACTICAL_TESTS in leaderboard.py.
    """
    from gptme.eval.leaderboard import BASIC_TESTS, PRACTICAL_TESTS
    from gptme.eval.suites import suites

    # Collect test names from all practical suites
    actual_practical = set()
    for suite_name, suite_tests in suites.items():
        if suite_name.startswith("practical"):
            for test in suite_tests:
                actual_practical.add(test["name"])

    # Collect test names from basic suite
    actual_basic = set()
    for test in suites.get("basic", []):
        actual_basic.add(test["name"])

    missing_practical = actual_practical - PRACTICAL_TESTS
    extra_practical = PRACTICAL_TESTS - actual_practical
    missing_basic = actual_basic - BASIC_TESTS
    extra_basic = BASIC_TESTS - actual_basic

    errors = []
    if missing_practical:
        errors.append(
            f"Tests in practical suites but not in PRACTICAL_TESTS: {sorted(missing_practical)}"
        )
    if extra_practical:
        errors.append(
            f"Tests in PRACTICAL_TESTS but not in any practical suite: {sorted(extra_practical)}"
        )
    if missing_basic:
        errors.append(
            f"Tests in basic suite but not in BASIC_TESTS: {sorted(missing_basic)}"
        )
    if extra_basic:
        errors.append(
            f"Tests in BASIC_TESTS but not in any basic suite: {sorted(extra_basic)}"
        )

    assert not errors, "\n".join(errors)
