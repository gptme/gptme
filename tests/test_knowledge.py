"""Tests for the cross-session knowledge base (gptme.knowledge + gptme-util knowledge CLI)."""

import json

import pytest
from click.testing import CliRunner

from gptme.cli.util import main


@pytest.fixture(autouse=True)
def isolated_kb(tmp_path, monkeypatch):
    """Redirect the knowledge store to a temp directory for every test."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Clear the lru_cache on get_data_dir so it picks up the new env var.
    from gptme import dirs

    if hasattr(dirs.get_data_dir, "cache_clear"):
        dirs.get_data_dir.cache_clear()
    yield
    if hasattr(dirs.get_data_dir, "cache_clear"):
        dirs.get_data_dir.cache_clear()


# ---------------------------------------------------------------------------
# Core module tests
# ---------------------------------------------------------------------------


def test_save_and_list():
    from gptme.knowledge import knowledge_list, knowledge_save

    entry = knowledge_save("test problem", "test resolution", tags=["pytest"])
    assert entry["memory_type"] == "knowledge_entry"
    assert entry["problem"] == "test problem"
    assert entry["resolution"] == "test resolution"
    assert "pytest" in entry["tags"]
    assert entry["id"]

    entries = knowledge_list()
    assert len(entries) == 1
    assert entries[0]["id"] == entry["id"]


def test_save_validates_empty_fields():
    from gptme.knowledge import knowledge_save

    with pytest.raises(ValueError, match="problem"):
        knowledge_save("", "some resolution")
    with pytest.raises(ValueError, match="resolution"):
        knowledge_save("some problem", "")


def test_search_returns_relevant():
    from gptme.knowledge import knowledge_save, knowledge_search

    knowledge_save("pytest test discovery fails", "prefix test function with test_")
    knowledge_save("git merge conflict resolution", "use git mergetool")
    knowledge_save("something unrelated", "other answer")

    results = knowledge_search("pytest discovery")
    assert results
    assert results[0]["problem"] == "pytest test discovery fails"


def test_search_validates_empty_query():
    from gptme.knowledge import knowledge_search

    with pytest.raises(ValueError, match="query"):
        knowledge_search("")


def test_search_tag_filter():
    from gptme.knowledge import knowledge_save, knowledge_search

    knowledge_save("problem A", "resolution A", tags=["git"])
    knowledge_save("problem B", "resolution B", tags=["pytest"])

    results = knowledge_search("problem", tags=["git"])
    assert len(results) == 1
    assert results[0]["problem"] == "problem A"


def test_list_tag_filter():
    from gptme.knowledge import knowledge_list, knowledge_save

    knowledge_save("problem A", "resolution A", tags=["git"])
    knowledge_save("problem B", "resolution B", tags=["pytest"])

    entries = knowledge_list(tags=["git"])
    assert len(entries) == 1
    assert entries[0]["problem"] == "problem A"


def test_list_newest_first():
    from gptme.knowledge import knowledge_list, knowledge_save

    knowledge_save("older problem", "older resolution")
    knowledge_save("newer problem", "newer resolution")

    entries = knowledge_list()
    assert entries[0]["problem"] == "newer problem"


def test_delete():
    from gptme.knowledge import knowledge_delete, knowledge_list, knowledge_save

    entry = knowledge_save("to delete", "resolution")
    assert knowledge_delete(entry["id"])
    assert knowledge_list() == []

    assert not knowledge_delete("nonexistent-id")


def test_jsonl_persistence(tmp_path, monkeypatch):
    """Entries survive across separate function call sequences (JSONL is durable)."""
    from gptme.knowledge import _entries_file, knowledge_save

    knowledge_save("durable problem", "durable resolution")
    path = _entries_file()
    assert path.exists()

    # Parse the raw JSONL line to confirm structure
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["memory_type"] == "knowledge_entry"
    assert parsed["problem"] == "durable problem"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_save_basic():
    runner = CliRunner()
    result = runner.invoke(main, ["knowledge", "save", "a problem", "a resolution"])
    assert result.exit_code == 0, result.output
    assert "Saved knowledge entry" in result.output


def test_cli_save_with_tags():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "knowledge",
            "save",
            "tagged problem",
            "tagged resolution",
            "-t",
            "git",
            "-t",
            "pytest",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Tags: git, pytest" in result.output


def test_cli_save_json_output():
    runner = CliRunner()
    result = runner.invoke(
        main, ["knowledge", "save", "--json", "json problem", "json resolution"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["problem"] == "json problem"
    assert data["memory_type"] == "knowledge_entry"


def test_cli_search():
    runner = CliRunner()
    runner.invoke(
        main, ["knowledge", "save", "pytest discovery problem", "prefix with test_"]
    )
    result = runner.invoke(main, ["knowledge", "search", "pytest"])
    assert result.exit_code == 0, result.output
    assert "pytest discovery problem" in result.output


def test_cli_search_no_results():
    runner = CliRunner()
    result = runner.invoke(main, ["knowledge", "search", "completely obscure term xyz"])
    assert result.exit_code == 0
    assert "No matching" in result.output


def test_cli_search_json():
    runner = CliRunner()
    runner.invoke(main, ["knowledge", "save", "search json problem", "resolution"])
    result = runner.invoke(main, ["knowledge", "search", "--json", "search json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["problem"] == "search json problem"


def test_cli_list():
    runner = CliRunner()
    runner.invoke(main, ["knowledge", "save", "listed problem", "listed resolution"])
    result = runner.invoke(main, ["knowledge", "list"])
    assert result.exit_code == 0, result.output
    assert "listed problem" in result.output


def test_cli_list_empty():
    runner = CliRunner()
    result = runner.invoke(main, ["knowledge", "list"])
    assert result.exit_code == 0
    assert "No entries" in result.output


def test_cli_list_json():
    runner = CliRunner()
    runner.invoke(main, ["knowledge", "save", "list json problem", "resolution"])
    result = runner.invoke(main, ["knowledge", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["problem"] == "list json problem"


def test_cli_delete():
    from gptme.knowledge import knowledge_save

    runner = CliRunner()
    # Use the module API to save so we get a clean ID without CLI noise
    entry = knowledge_save("delete me", "resolution")
    entry_id = entry["id"]

    # Delete by prefix (first 8 chars)
    result = runner.invoke(main, ["knowledge", "delete", entry_id[:8]])
    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output

    # Confirm it's gone
    result = runner.invoke(main, ["knowledge", "list"])
    assert "No entries" in result.output


def test_cli_delete_nonexistent():
    runner = CliRunner()
    result = runner.invoke(main, ["knowledge", "delete", "nonexistent"])
    assert result.exit_code != 0
    assert "No entry found" in result.output
