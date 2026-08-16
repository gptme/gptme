"""Tests for `gptme-util explain`."""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_explain import (
    explain,
    find_topic,
    load_faq,
    suggest_topics,
)


@pytest.fixture
def entries():
    return load_faq()


@pytest.fixture
def runner():
    return CliRunner()


def test_faq_loads_required_topics(entries):
    topics = {entry.topic for entry in entries}
    assert {"branches", "context", "logs", "models", "tools"} <= topics


def test_faq_entries_are_complete(entries):
    for entry in entries:
        assert entry.question.strip(), f"{entry.topic} missing question"
        assert entry.answer.strip(), f"{entry.topic} missing answer"


def test_see_also_references_real_topics(entries):
    topics = {entry.topic for entry in entries}
    for entry in entries:
        assert set(entry.see_also) <= topics, f"{entry.topic} points at unknown topic"


def test_topic_ids_and_aliases_are_unique(entries):
    seen: dict[str, str] = {}
    for entry in entries:
        for name in entry.names:
            assert name not in seen, (
                f"{name!r} claimed by {seen.get(name)} and {entry.topic}"
            )
            seen[name] = entry.topic


def test_normalized_aliases_are_unique(entries):
    """Normalized aliases must be unique across all topics.

    ``suggest_topics`` builds a ``{normalized_alias: topic}`` dict where a
    collision silently overwrites the earlier topic, causing incorrect or
    incomplete suggestions. Raw-name uniqueness (tested above) is not enough —
    'context-window' and 'context window' are distinct raw names but normalize
    to the same string.
    """
    from gptme.cli.cmd_explain import _normalize

    seen: dict[str, str] = {}
    for entry in entries:
        for name in entry.names:
            norm = _normalize(name)
            assert norm not in seen, (
                f"normalized alias {norm!r} (from {name!r}) collides: "
                f"already claimed by {seen[norm]!r}, now also by {entry.topic!r}"
            )
            seen[norm] = entry.topic


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("branches", "branches"),
        ("Branches", "branches"),
        ("branch", "branches"),  # alias
        ("context", "context"),
        ("token limit", "context"),  # multi-word alias
        ("what are tools", "tools"),  # keyword overlap, not exact
        ("what are commands", "commands"),  # plural query, singular alias
        # Regression: generic question words ("where", "stored") must not
        # override a direct topic-name match ("config") via question-text overlap.
        ("where is config stored", "config"),
        # Natural-language model query should beat "context" (whose question
        # says "how do I" too) because "model" is in the models aliases.
        ("how do I change the model", "models"),
    ],
)
def test_find_topic(query, expected, entries):
    match = find_topic(query, entries)
    assert match is not None and match.topic == expected


def test_find_topic_returns_none_for_nonsense(entries):
    assert find_topic("zzzzqqq", entries) is None
    assert find_topic("", entries) is None


def test_suggest_topics_on_typo(entries):
    assert "branches" in suggest_topics("branchez", entries)


def test_explain_known_topic(runner):
    result = runner.invoke(explain, ["branches"])
    assert result.exit_code == 0
    assert "branch" in result.output.lower()


def test_explain_lists_topics_without_args(runner):
    result = runner.invoke(explain, [])
    assert result.exit_code == 0
    assert "branches" in result.output
    assert "context" in result.output


def test_explain_unknown_topic_suggests_and_fails(runner):
    result = runner.invoke(explain, ["branchez"])
    assert result.exit_code == 1
    assert "Did you mean" in result.output


def test_explain_json_output(runner):
    result = runner.invoke(explain, ["--json", "branches"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["topic"] == "branches"
    assert payload["answer"]


def test_explain_json_list(runner):
    result = runner.invoke(explain, ["--json"])
    assert result.exit_code == 0
    assert len(json.loads(result.output)) >= 5


def test_explain_json_unknown_topic(runner):
    result = runner.invoke(explain, ["--json", "zzzzqqq"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["match"] is None
    assert payload["suggestions"]


def test_explain_registered_in_util_group():
    from gptme.cli.util import UTIL_SUBCOMMANDS

    assert "explain" in UTIL_SUBCOMMANDS


# Regression: P1 — load_faq raises a friendly ClickException on broken data file
def test_load_faq_missing_file_raises_friendly_error(tmp_path):
    """Missing FAQ file produces a readable error, not a raw FileNotFoundError traceback."""
    missing = tmp_path / "does_not_exist.yaml"
    from click import ClickException

    with pytest.raises(ClickException, match="Could not read FAQ file"):
        load_faq.__wrapped__(str(missing))  # bypass lru_cache


def test_load_faq_malformed_yaml_raises_friendly_error(tmp_path, runner):
    """Malformed YAML produces a readable error, not a raw yaml.YAMLError traceback."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("topics: [\n  - {unclosed")
    from click import ClickException

    with pytest.raises(ClickException, match="Could not parse FAQ file"):
        load_faq.__wrapped__(str(bad_yaml))  # bypass lru_cache


@pytest.mark.parametrize(
    "content",
    [
        "topics: invalid",
        "topics:\n  - question: Missing topic\n    answer: Broken entry",
    ],
)
def test_load_faq_invalid_structure_raises_friendly_error(tmp_path, content):
    """Structurally invalid YAML produces a readable error, not a raw traceback."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(content)
    from click import ClickException

    with pytest.raises(ClickException, match="Invalid FAQ structure"):
        load_faq.__wrapped__(str(bad_yaml))  # bypass lru_cache


# Regression: P2 — an empty FAQ gives useful output for both command branches
def test_explain_list_with_empty_faq_does_not_crash(runner):
    """Empty FAQ entries list must not raise ValueError from max() with no default."""
    with patch("gptme.cli.cmd_explain.load_faq", return_value=[]):
        result = runner.invoke(explain, [])
    assert result.exit_code == 0
    assert "No topics available" in result.output


def test_explain_unknown_topic_with_empty_faq_has_no_blank_suggestion(runner):
    with patch("gptme.cli.cmd_explain.load_faq", return_value=[]):
        result = runner.invoke(explain, ["branches"])
    assert result.exit_code == 1
    assert "No topics available" in result.output
    assert "Did you mean" not in result.output
