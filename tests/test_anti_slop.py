"""Tests for the gptme anti-slop detector and quality gate."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gptme.anti_slop import (
    DEFAULT_MODE,
    MIN_WORDS_FOR_GATE,
    MODES,
    detect_smells,
    evaluate_gate,
)
from gptme.cli.cmd_slop import slop as anti_slop

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal clean text that clears the MIN_WORDS_FOR_GATE threshold (≥20 words).
_CLEAN_20W = (
    "The selector claims task work through a coordination claim and exits cleanly "
    "when the claim is denied by another active session holder."
)

# Dense-slop text that clears the threshold and scores high.
_SLOP_20W = (
    "Certainly! It's worth noting that we delve into a rich tapestry of ideas "
    "in order to navigate the complexities of the ever-evolving landscape here."
)

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
    # Tight tolerance (1/1k) → excess dashes flagged
    r_strict = detect_smells(text, em_dash_tolerance=1.0)
    # Loose tolerance (8/1k) → fewer excess
    r_relaxed = detect_smells(text, em_dash_tolerance=8.0)
    strict_em = r_strict.get("by_category", {}).get("em_dash", 0)
    relaxed_em = r_relaxed.get("by_category", {}).get("em_dash", 0)
    assert relaxed_em <= strict_em


def test_detect_returns_word_count():
    report = detect_smells("one two three four five")
    assert report["word_count"] == 5


# ---------------------------------------------------------------------------
# evaluate_gate — short text / empty input
# ---------------------------------------------------------------------------


def test_gate_skips_short_text():
    """Texts below MIN_WORDS_FOR_GATE return status='skip'."""
    report = evaluate_gate("too short")
    assert report["status"] == "skip"
    assert str(MIN_WORDS_FOR_GATE) in report["reason"]


def test_gate_skips_empty_text():
    report = evaluate_gate("")
    assert report["status"] == "skip"
    assert report["smell_report"]["word_count"] == 0


def test_gate_skips_at_boundary():
    # Exactly MIN_WORDS_FOR_GATE - 1 words → skip
    text = " ".join(["word"] * (MIN_WORDS_FOR_GATE - 1))
    assert evaluate_gate(text)["status"] == "skip"

    # Exactly MIN_WORDS_FOR_GATE words → scored (pass for clean text)
    text = " ".join(["word"] * MIN_WORDS_FOR_GATE)
    assert evaluate_gate(text)["status"] == "pass"


# ---------------------------------------------------------------------------
# evaluate_gate — scoring and modes
# ---------------------------------------------------------------------------


def test_gate_passes_clean_text():
    report = evaluate_gate(_CLEAN_20W)
    assert report["status"] == "pass"


def test_gate_default_mode_is_balanced():
    report = evaluate_gate(_CLEAN_20W)
    assert report["mode"] == DEFAULT_MODE == "balanced"
    assert report["thresholds"]["warn"] == MODES["balanced"]["warn"]
    assert report["thresholds"]["fail"] == MODES["balanced"]["fail"]


def test_gate_fails_on_dense_tells():
    report = evaluate_gate(_SLOP_20W)
    assert report["status"] == "fail"


def test_gate_mode_strict_lower_thresholds():
    r = evaluate_gate(_CLEAN_20W, mode="strict")
    assert r["thresholds"]["warn"] == MODES["strict"]["warn"]
    assert r["thresholds"]["warn"] < MODES["balanced"]["warn"]


def test_gate_mode_relaxed_higher_thresholds():
    r = evaluate_gate(_CLEAN_20W, mode="relaxed")
    assert r["thresholds"]["warn"] == MODES["relaxed"]["warn"]
    assert r["thresholds"]["warn"] > MODES["balanced"]["warn"]


def test_gate_explicit_threshold_overrides_mode():
    r = evaluate_gate(
        _CLEAN_20W, mode="strict", warn_threshold=999.0, fail_threshold=1000.0
    )
    assert r["thresholds"]["warn"] == 999.0
    assert r["thresholds"]["fail"] == 1000.0


def test_gate_rejects_inverted_thresholds():
    with pytest.raises(ValueError, match="fail_threshold"):
        evaluate_gate(_CLEAN_20W, warn_threshold=5.0, fail_threshold=5.0)


def test_gate_rejects_unknown_mode():
    with pytest.raises(ValueError, match="unknown mode"):
        evaluate_gate(_CLEAN_20W, mode="typo")


def test_gate_report_structure():
    r = evaluate_gate(_CLEAN_20W)
    assert set(r) >= {"status", "reason", "mode", "thresholds", "smell_report"}
    assert set(r["thresholds"]) >= {"warn", "fail", "em_dash_tolerance"}


# ---------------------------------------------------------------------------
# CLI (gptme slop check)
# ---------------------------------------------------------------------------


def test_cli_check_clean_text():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", _CLEAN_20W])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_cli_check_skips_short_text():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", "Too short."])
    assert result.exit_code == 0
    assert "SKIP" in result.output


def test_cli_check_fails_on_slop():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", _SLOP_20W])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_check_no_fail_always_exits_zero():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", _SLOP_20W, "--no-fail"])
    assert result.exit_code == 0


def test_cli_check_json_output():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", _CLEAN_20W, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "status" in data
    assert "smell_report" in data


def test_cli_check_mode_strict():
    runner = CliRunner()
    result = runner.invoke(
        anti_slop, ["check", "--text", _CLEAN_20W, "--mode", "strict", "--json"]
    )
    data = json.loads(result.output)
    assert data["mode"] == "strict"
    assert data["thresholds"]["warn"] == MODES["strict"]["warn"]


def test_cli_check_invalid_thresholds_raises_usage_error():
    runner = CliRunner()
    result = runner.invoke(
        anti_slop,
        [
            "check",
            "--text",
            _CLEAN_20W,
            "--warn-threshold",
            "5",
            "--fail-threshold",
            "5",
        ],
    )
    assert result.exit_code != 0
    assert "Error" in result.output


def test_cli_check_negative_top_rejected():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", "--text", _CLEAN_20W, "--top", "-1"])
    assert result.exit_code != 0


def test_cli_check_file(tmp_path):
    p = tmp_path / "draft.md"
    p.write_text(_CLEAN_20W + "\n")
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check", str(p)])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_cli_check_stdin():
    runner = CliRunner()
    result = runner.invoke(anti_slop, ["check"], input=_CLEAN_20W + "\n")
    assert result.exit_code == 0
