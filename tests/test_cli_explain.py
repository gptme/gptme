"""Tests for `gptme-util explain`."""

import json

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


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("branches", "branches"),
        ("Branches", "branches"),
        ("branch", "branches"),  # alias
        ("context", "context"),
        ("token limit", "context"),  # multi-word alias
        ("what are tools", "tools"),  # keyword overlap, not exact
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
