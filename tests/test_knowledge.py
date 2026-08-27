"""Tests for the cross-session knowledge base (gptme.knowledge + gptme-util knowledge CLI)."""

import json

import pytest
from click.testing import CliRunner

from gptme.cli.util import main


@pytest.fixture(autouse=True)
def isolated_kb(tmp_path, monkeypatch):
    """Redirect the knowledge store and disable external indexing in tests."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("gptme.cli.cmd_knowledge.shutil.which", lambda _: None)
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


def test_search_validates_arguments():
    from gptme.knowledge import knowledge_search

    with pytest.raises(ValueError, match="query"):
        knowledge_search("")
    with pytest.raises(ValueError, match="top_k"):
        knowledge_search("query", top_k=-1)


def test_search_matches_single_character_term():
    from gptme.knowledge import knowledge_save, knowledge_search

    entry = knowledge_save("x server failure", "restart x")

    assert knowledge_search("x") == [entry]


def test_search_matches_numeric_identifier():
    from gptme.knowledge import knowledge_save, knowledge_search

    entry = knowledge_save("request failed", "server returned 404")

    assert knowledge_search("404") == [entry]


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


def test_load_entries_skips_malformed_objects():
    from gptme.knowledge import _entries_file, knowledge_list, knowledge_save

    entry = knowledge_save("valid problem", "valid resolution")
    with _entries_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": "../../outside"}) + "\n")
        f.write(json.dumps({"id": str(entry["id"])}) + "\n")
        invalid_tags = {
            **entry,
            "id": "e8048c53-c70a-4e16-9660-820b9bea29f8",
            "tags": [1],
        }
        f.write(json.dumps(invalid_tags) + "\n")

    assert knowledge_list() == [entry]


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


@pytest.mark.parametrize("command", ["save", "delete"])
def test_cli_rag_index_oserror_is_nonfatal(monkeypatch, command):
    from gptme.knowledge import knowledge_save

    monkeypatch.setattr("gptme.cli.cmd_knowledge.shutil.which", lambda _: "gptme-rag")
    monkeypatch.setattr("gptme.cli.cmd_knowledge._export_for_rag", lambda _: None)

    def fail(*args, **kwargs):
        raise PermissionError("cannot execute gptme-rag")

    monkeypatch.setattr("gptme.cli.cmd_knowledge.subprocess.run", fail)
    runner = CliRunner()
    if command == "save":
        result = runner.invoke(main, ["knowledge", "save", "problem", "resolution"])
    else:
        entry = knowledge_save("problem", "resolution")
        result = runner.invoke(main, ["knowledge", "delete", entry["id"]])

    assert result.exit_code == 0, result.output
    assert "Warning: gptme-rag" in result.output
    assert "cannot execute gptme-rag" in result.output


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


def test_cli_human_output_strips_control_characters():
    runner = CliRunner()
    save_result = runner.invoke(
        main,
        [
            "knowledge",
            "save",
            "unsafe\x1b[2J problem",
            "unsafe\x07 resolution",
            "-t",
            "tag\x1b[31m",
        ],
    )

    search_result = runner.invoke(main, ["knowledge", "search", "unsafe"])
    list_result = runner.invoke(main, ["knowledge", "list"])

    assert save_result.exit_code == 0, save_result.output
    assert search_result.exit_code == 0, search_result.output
    assert list_result.exit_code == 0, list_result.output
    assert "\x1b" not in save_result.output
    assert "\x1b" not in search_result.output
    assert "\x07" not in search_result.output
    assert "\x1b" not in list_result.output


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


def test_cli_list_reports_io_error(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("cannot read store")

    monkeypatch.setattr("gptme.knowledge.knowledge_list", fail)
    runner = CliRunner()

    result = runner.invoke(main, ["knowledge", "list"])

    assert result.exit_code != 0
    assert "Error: cannot read store" in result.output
    assert not isinstance(result.exception, OSError)


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


def test_cli_delete_skips_reindex_when_mirror_removal_fails(monkeypatch):
    from gptme.knowledge import _knowledge_dir, knowledge_save

    entry = knowledge_save("delete me", "resolution")
    mirror = _knowledge_dir() / "rag" / f"{entry['id']}.md"
    mirror.parent.mkdir(parents=True)
    mirror.write_text("stale", encoding="utf-8")
    monkeypatch.setattr("gptme.cli.cmd_knowledge.shutil.which", lambda _: "gptme-rag")

    def fail_unlink(*args, **kwargs):
        raise PermissionError("cannot remove mirror")

    monkeypatch.setattr(type(mirror), "unlink", fail_unlink)
    calls = []
    monkeypatch.setattr(
        "gptme.cli.cmd_knowledge.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = CliRunner().invoke(main, ["knowledge", "delete", entry["id"]])

    assert result.exit_code == 0, result.output
    assert "could not remove mirror" in result.output
    assert calls == []


def test_cli_delete_nonexistent():
    runner = CliRunner()
    result = runner.invoke(main, ["knowledge", "delete", "nonexistent"])
    assert result.exit_code != 0
    assert "No entry found" in result.output
