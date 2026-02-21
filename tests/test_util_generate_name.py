"""Tests for the generate_name utility module."""

from gptme.util.generate_name import (
    actions,
    adjectives,
    generate_name,
    is_generated_name,
    nouns,
)


def test_no_duplicate_actions():
    """All entries in the actions list should be unique."""
    assert len(actions) == len(
        set(actions)
    ), f"Duplicate actions: {[a for a in actions if actions.count(a) > 1]}"


def test_no_duplicate_adjectives():
    """All entries in the adjectives list should be unique."""
    assert len(adjectives) == len(
        set(adjectives)
    ), f"Duplicate adjectives: {[a for a in adjectives if adjectives.count(a) > 1]}"


def test_no_duplicate_nouns():
    """All entries in the nouns list should be unique."""
    assert len(nouns) == len(
        set(nouns)
    ), f"Duplicate nouns: {[n for n in nouns if nouns.count(n) > 1]}"


def test_generate_name_format():
    """Generated names should have the action-adjective-noun format."""
    for _ in range(50):
        name = generate_name()
        parts = name.split("-")
        assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}: {name}"
        assert parts[0] in actions, f"Unknown action: {parts[0]}"
        assert parts[1] in adjectives, f"Unknown adjective: {parts[1]}"
        assert parts[2] in nouns, f"Unknown noun: {parts[2]}"


def test_generate_name_uniqueness():
    """Generate many names and verify reasonable uniqueness."""
    names = {generate_name() for _ in range(100)}
    # With 14*18*28 = 7056 possible combos, 100 samples should mostly be unique
    assert len(names) > 80, f"Too many duplicates: only {len(names)} unique out of 100"


def test_is_generated_name_positive():
    """is_generated_name should recognize names from generate_name."""
    for _ in range(50):
        name = generate_name()
        assert is_generated_name(name), f"Failed to recognize: {name}"


def test_is_generated_name_negative():
    """is_generated_name should reject non-generated strings."""
    assert not is_generated_name("hello-world")
    assert not is_generated_name("not-a-valid-name")
    assert not is_generated_name("just-one")
    assert not is_generated_name("too-many-parts-here")
    assert not is_generated_name("")
    assert not is_generated_name("running-happy")  # only 2 parts


def test_word_lists_nonempty():
    """All word lists should have entries."""
    assert len(actions) >= 10
    assert len(adjectives) >= 10
    assert len(nouns) >= 10
