"""Tests for the gptme anti-slop detector and quality gate."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gptme.anti_slop import DEFAULT_MODE, MODES, detect_smells, evaluate_gate
from gptme.cli.cmd_slop import slop as anti_slop

# ---------------------------------------------------------------------------
# detect_smells
# ---------------------------------------------------------------------------


def test_detect_clean_text_scores_zero():
    report = detect_smells(
        "The selector claims task work through coordination and exits when "
        "the claim is denied."
    )
    assert report["weighted_score"] == 0.0
    assert report["total_hits"] == 0


def test_detect_high_confidence_tells():
    report = detect_smells(
        "It's worth noting that we delve into a rich tapestry of ideas."
    )
    labels = {h["label"] for h in report["hits"]}
    assert "delve" in labels
    assert "it's worth noting" in labels
    assert report["weighted_score"] > 0


def test_detect_em_dash_tolerance():
    # 100 words of clean text with 6 em-dashes
    text = ("word " * 100).strip() + " — " * 6
    # Tight tolerance (1/1k) → 5 excess dashes flagged
    r_strict = detect_smells(text, em_dash_tolerance=1.0)
    # Loose tolerance (8/1k) → none flagged (6 tolerated for 100 words ~ round(0.8)=1 → no, still 5 excess with tolerance=1)
    r_relaxed = detect_smells(text, em_dash_tolerance=8.0)
    # Relaxed has lower em_dash contribution
    strict_em = r_strict.get("by_category", {}).get("em_dash", 0)
    relaxed_em = r_relaxed.get("by_category", {}).get("em_dash", 0)
    assert relaxed_em <= strict_em


def test_detect_returns_word_count():
    report = detect_smells("one two three four five")
    assert report["word_count"] == 5


# ---------------------------------------------------------------------------
# evaluate_gate
# ---------------------------------------------------------------------------


def test_gate_passes_clean_text():
    report = evaluate_gate(
        "The selector claims task work through coordination. Tests cover behavior."
    )
    assert report["status"] == "pass"


def test_gate_default_mode_is_balanced():
    report = evaluate_gate("clean text")
    assert report["mode"] == DEFAULT_MODE == "balanced"
    assert report["thresholds"]["warn"] == MODES["balanced"]["warn"]
    assert report["thresholds"]["fail"] == MODES["balanced"]["fail"]


def test_gate_fails_on_dense_tells():
    report = evaluate_gate(
        "Certainly! It's worth noting that we delve into a rich tapestry of ideas."
    )
    assert report["status"] == "fail"


def test_gate_mode_strict_lower_thresholds():
    r = evaluate_gate("clean", mode="strict")
    assert r["thresholds"]["warn"] == MODES["strict"]["warn"]
    assert r["thresholds"]["warn"] < MODES["balanced"]["warn"]


def test_gate_mode_relaxed_higher_thresholds():
    r = evaluate_gate("clean", mode="relaxed")
    assert r["thresholds"]["warn"] == MODES["relaxed"]["warn"]
    assert r["thresholds"]["warn"] > MODES["balanced"]["warn"]


def test_gate_explicit_threshold_overrides_mode():
    r = evaluate_gate(
        "clean", mode="strict", warn_threshold=999.0, fail_threshold=1000.0
    )
    assert r["thresholds"]["warn"] == 999.0
    assert r["thresholds"]["fail"] == 1000.0


def test_gate_rejects_inverted_thresholds():
    with pytest.raises(ValueError, match="fail_threshold"):
        evaluate_gate("text", warn_threshold=5.0, fail_threshold=5.0)


def test_gate_report_structure():
    r = evaluate_gate("clean technical prose")
    assert set(r) >= {"status", "reason", "mode", "thresholds", "smell_report"}
    assert set(r["thresholds"]) >= {"warn", "fail", "em_dash_tolerance"}


# ---------------------------------------------------------------------------
# CLI (gptme-anti-slop check)
# ---------------------------------------------------------------------------


def test_cli_check_clean_text():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", "Clean technical prose."])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_cli_check_fails_on_slop():
    runner = CliRunner()
    result = runner.invoke(
        anti_slop,
        [
            "check",
            "--text",
            "Certainly! It's worth noting that we delve into a tapestry.",
        ],
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_check_no_fail_always_exits_zero():
    runner = CliRunner()
    result = runner.invoke(
        anti_slop,
        [
            "check",
            "--text",
            "Certainly! It's worth noting that we delve into a tapestry.",
            "--no-fail",
        ],
    )
    assert result.exit_code == 0


def test_cli_check_json_output():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", "Clean text.", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "status" in data
    assert "smell_report" in data


def test_cli_check_mode_strict():
    runner = CliRunner()
    result = runner.invoke(
        anti_slop, ["check", "--text", "Clean text.", "--mode", "strict", "--json"]
    )
    data = json.loads(result.output)
    assert data["mode"] == "strict"
    assert data["thresholds"]["warn"] == MODES["strict"]["warn"]


def test_cli_check_file(tmp_path):
    p = tmp_path / "draft.md"
    p.write_text("Clean prose with no AI tells.\n")
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", str(p)])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_cli_check_stdin():
    runner = CliRunner()
    result = runner.invoke(
        anti_slop, ["check"], input="Clean text piped through stdin.\n"
    )
    assert result.exit_code == 0
