"""Tests for the eval trends tracking script."""

import csv
from pathlib import Path

from gptme.eval.trends import (
    compute_diff,
    compute_trends,
    format_diff,
    format_table,
    load_all_results,
    parse_run_timestamp,
)


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


def test_parse_run_timestamp():
    """Parse directory name into datetime."""
    ts = parse_run_timestamp("20260323_050922Z")
    assert ts.year == 2026
    assert ts.month == 3
    assert ts.day == 23
    assert ts.hour == 5
    assert ts.minute == 9


def test_parse_run_timestamp_invalid():
    """Invalid timestamp returns datetime.min."""
    ts = parse_run_timestamp("not-a-timestamp")
    assert ts.year == 1


def test_load_all_results(tmp_path):
    """Loading results includes timestamps and sorts chronologically."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260322_120000Z",
                "rows": [
                    ("openai/gpt-4o", "markdown", "hello", "true"),
                ],
            },
            {
                "dir": "20260323_120000Z",
                "rows": [
                    ("openai/gpt-4o", "markdown", "hello", "false"),
                ],
            },
        ],
    )
    results = load_all_results(tmp_path)
    assert len(results) == 2
    assert results[0]["timestamp"] < results[1]["timestamp"]
    assert results[0]["passed"] is True
    assert results[1]["passed"] is False


def test_detect_regression(tmp_path):
    """Detect a test that was passing but now fails."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "true"),
                ],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "false"),
                ],
            },
        ],
    )
    results = load_all_results(tmp_path)
    trends = compute_trends(results)
    assert len(trends["regressions"]) == 1
    assert trends["regressions"][0]["test"] == "prime100"
    assert trends["regressions"][0]["latest_passed"] is False
    assert trends["regressions"][0]["previous_passed"] is True


def test_detect_improvement(tmp_path):
    """Detect a test that was failing but now passes."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "false"),
                ],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                ],
            },
        ],
    )
    results = load_all_results(tmp_path)
    trends = compute_trends(results)
    assert len(trends["improvements"]) == 1
    assert trends["improvements"][0]["test"] == "hello"


def test_stable_pass(tmp_path):
    """Tests that consistently pass are marked stable."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [("model-a", "markdown", "hello", "true")],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [("model-a", "markdown", "hello", "true")],
            },
        ],
    )
    results = load_all_results(tmp_path)
    trends = compute_trends(results)
    assert len(trends["stable_pass"]) == 1
    assert len(trends["regressions"]) == 0


def test_model_filter(tmp_path):
    """Model filter limits results to matching models."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-b", "tool", "hello", "false"),
                ],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "false"),
                    ("model-b", "tool", "hello", "true"),
                ],
            },
        ],
    )
    results = load_all_results(tmp_path)
    trends = compute_trends(results, model_filter="model-a")
    assert len(trends["regressions"]) == 1
    assert trends["regressions"][0]["model"] == "model-a@markdown"
    # model-b should not appear
    all_models = set()
    for category in ["regressions", "improvements", "stable_pass", "stable_fail"]:
        for entry in trends[category]:
            all_models.add(entry["model"])
    assert all(m.startswith("model-a") for m in all_models)


def test_last_n_runs(tmp_path):
    """last_n_runs limits the window considered."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [("model-a", "markdown", "hello", "true")],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [("model-a", "markdown", "hello", "false")],
            },
            {
                "dir": "20260103_000000Z",
                "rows": [("model-a", "markdown", "hello", "true")],
            },
        ],
    )
    results = load_all_results(tmp_path)
    # With last 2 runs: false -> true = improvement
    trends = compute_trends(results, last_n_runs=2)
    assert len(trends["improvements"]) == 1
    # With all 3 runs: true -> false -> true, latest vs previous = improvement
    trends = compute_trends(results)
    assert len(trends["improvements"]) == 1


def test_compute_diff(tmp_path):
    """Diff compares the two most recent runs per model."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "false"),
                ],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "prime100", "true"),
                    ("model-a", "markdown", "new-test", "true"),
                ],
            },
        ],
    )
    results = load_all_results(tmp_path)
    diffs = compute_diff(results)
    assert "model-a@markdown" in diffs
    d = diffs["model-a@markdown"]
    assert "prime100" in d["gained"]
    assert len(d["lost"]) == 0
    assert d["unchanged_pass"] == 1  # hello
    assert len(d["new_tests"]) == 1  # new-test


def test_compute_diff_removed_tests(tmp_path):
    """Tests present in the previous run but absent from the latest run appear in removed_tests."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    ("model-a", "markdown", "deprecated-test", "true"),
                ],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [
                    ("model-a", "markdown", "hello", "true"),
                    # deprecated-test is absent from the latest run
                ],
            },
        ],
    )
    results = load_all_results(tmp_path)
    diffs = compute_diff(results)
    assert "model-a@markdown" in diffs
    d = diffs["model-a@markdown"]
    assert any(r["test"] == "deprecated-test" for r in d["removed_tests"])
    assert d["unchanged_pass"] == 1  # hello


def test_format_table_output(tmp_path):
    """Format table produces readable output."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [("model-a", "markdown", "hello", "true")],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [("model-a", "markdown", "hello", "false")],
            },
        ],
    )
    results = load_all_results(tmp_path)
    trends = compute_trends(results)
    output = format_table(trends)
    assert "Regressions" in output
    assert "model-a" in output
    assert "Summary" in output


def test_format_diff_output(tmp_path):
    """Format diff produces readable output."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [("model-a", "markdown", "hello", "false")],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [("model-a", "markdown", "hello", "true")],
            },
        ],
    )
    results = load_all_results(tmp_path)
    diffs = compute_diff(results)
    output = format_diff(diffs)
    assert "model-a" in output
    assert "Gained" in output


def test_empty_results(tmp_path):
    """Empty dir returns empty results."""
    results = load_all_results(tmp_path)
    assert results == []


def test_flaky_detection(tmp_path):
    """Tests with intermittent results (pass_rate 10-90%) are detected as flaky."""
    rows_run1 = [("model-a", "markdown", "flaky-test", "true")]
    rows_run2 = [("model-a", "markdown", "flaky-test", "false")]
    rows_run3 = [("model-a", "markdown", "flaky-test", "true")]
    rows_run4 = [("model-a", "markdown", "flaky-test", "false")]
    rows_run5 = [("model-a", "markdown", "flaky-test", "true")]

    _create_eval_results(
        tmp_path,
        [
            {"dir": "20260101_000000Z", "rows": rows_run1},
            {"dir": "20260102_000000Z", "rows": rows_run2},
            {"dir": "20260103_000000Z", "rows": rows_run3},
            {"dir": "20260104_000000Z", "rows": rows_run4},
            {"dir": "20260105_000000Z", "rows": rows_run5},
        ],
    )
    results = load_all_results(tmp_path)
    trends = compute_trends(results)
    # pass_rate=0.6 (3 of 5) → classified as flaky regardless of last-two-run direction
    assert len(trends["flaky"]) == 1
    assert len(trends["improvements"]) == 0


def test_at_format_in_model_name(tmp_path):
    """Model names with @format suffix are handled correctly."""
    _create_eval_results(
        tmp_path,
        [
            {
                "dir": "20260101_000000Z",
                "rows": [("model-a@tool", "", "hello", "true")],
            },
            {
                "dir": "20260102_000000Z",
                "rows": [("model-a@tool", "", "hello", "false")],
            },
        ],
    )
    results = load_all_results(tmp_path)
    trends = compute_trends(results)
    assert len(trends["regressions"]) == 1
    assert trends["regressions"][0]["model"] == "model-a@tool"
