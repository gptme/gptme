"""Tests for natural pass-rate gate (Phase 3 of conditional lesson injection)."""

import json

import pytest

from gptme.eval.pass_rate_gate import (
    ENV_VAR,
    apply_gate,
    get_gate_recommendation,
    load_pass_rate_data,
)


@pytest.fixture
def sample_data():
    return {
        "summary": {"models": ["m1"], "evals": ["e1", "e2", "e3"]},
        "lookup": {
            "m1": {
                "e1": {
                    "baseline": 1.0,
                    "holdout": 0.83,
                    "delta": 0.17,
                    "effect": "lessons_help",
                    "gate_recommendation": "inject",
                },
                "e2": {
                    "baseline": 0.8,
                    "holdout": 1.0,
                    "delta": -0.2,
                    "effect": "lessons_hurt",
                    "gate_recommendation": "suppress",
                },
                "e3": {
                    "baseline": 0.6,
                    "holdout": 0.6,
                    "delta": 0.0,
                    "effect": "neutral",
                    "gate_recommendation": "default",
                },
            }
        },
    }


def test_load_returns_none_when_no_path_or_env(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert load_pass_rate_data() is None


def test_load_returns_none_for_missing_file(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    missing = tmp_path / "nope.json"
    assert load_pass_rate_data(missing) is None


def test_load_picks_up_env_var(tmp_path, monkeypatch, sample_data):
    p = tmp_path / "rates.json"
    p.write_text(json.dumps(sample_data))
    monkeypatch.setenv(ENV_VAR, str(p))
    data = load_pass_rate_data()
    assert data is not None
    assert "lookup" in data


def test_load_explicit_path_overrides_env(tmp_path, monkeypatch, sample_data):
    env_p = tmp_path / "env.json"
    env_p.write_text(json.dumps({"lookup": {}}))
    arg_p = tmp_path / "arg.json"
    arg_p.write_text(json.dumps(sample_data))
    monkeypatch.setenv(ENV_VAR, str(env_p))
    data = load_pass_rate_data(arg_p)
    assert data is not None
    assert "m1" in data["lookup"]


def test_load_raises_on_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json")
    with pytest.raises(ValueError, match="Invalid pass-rate gate JSON"):
        load_pass_rate_data(p)


def test_load_raises_when_missing_lookup_key(tmp_path):
    p = tmp_path / "noschema.json"
    p.write_text(json.dumps({"summary": {}}))
    with pytest.raises(ValueError, match="missing top-level 'lookup'"):
        load_pass_rate_data(p)


def test_get_gate_recommendation_no_data():
    assert get_gate_recommendation("any", "any", None) == "default"


def test_get_gate_recommendation_present(sample_data):
    assert get_gate_recommendation("m1", "e1", sample_data) == "inject"
    assert get_gate_recommendation("m1", "e2", sample_data) == "suppress"
    assert get_gate_recommendation("m1", "e3", sample_data) == "default"


def test_get_gate_recommendation_unknown_pair(sample_data):
    assert get_gate_recommendation("m1", "nonexistent", sample_data) == "default"
    assert get_gate_recommendation("unknown", "e1", sample_data) == "default"


def test_apply_gate_suppress_overrides_no_lessons_false(sample_data):
    eff, decision = apply_gate(
        model="m1", eval_name="e2", no_lessons=False, data=sample_data
    )
    assert eff is True
    assert decision == "suppress"


def test_apply_gate_inject_overrides_no_lessons_true(sample_data):
    eff, decision = apply_gate(
        model="m1", eval_name="e1", no_lessons=True, data=sample_data
    )
    assert eff is False
    assert decision == "inject"


def test_apply_gate_default_passes_through(sample_data):
    eff, decision = apply_gate(
        model="m1", eval_name="e3", no_lessons=True, data=sample_data
    )
    assert eff is True
    assert decision == "default"
    eff, decision = apply_gate(
        model="m1", eval_name="e3", no_lessons=False, data=sample_data
    )
    assert eff is False
    assert decision == "default"


def test_apply_gate_no_data_passes_through():
    eff, decision = apply_gate(model="m1", eval_name="e1", no_lessons=True, data=None)
    assert eff is True
    assert decision == "default"


def test_apply_gate_unknown_pair_passes_through(sample_data):
    eff, decision = apply_gate(
        model="m1", eval_name="not-in-lookup", no_lessons=False, data=sample_data
    )
    assert eff is False
    assert decision == "default"


def test_invalid_gate_value_treated_as_default(sample_data):
    sample_data["lookup"]["m1"]["e1"]["gate_recommendation"] = "weird"
    assert get_gate_recommendation("m1", "e1", sample_data) == "default"
