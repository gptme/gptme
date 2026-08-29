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


def test_search_considers_terms_after_thirtieth():
    from gptme.knowledge import knowledge_save, knowledge_search

    entry = knowledge_save("zlib failure", "rebuild the archive")
    query = " ".join([*(f"term{i}" for i in range(30)), "zlib"])

    assert knowledge_search(query) == [entry]


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


@pytest.mark.parametrize(
    "delete_func", ["knowledge_delete", "knowledge_delete_by_prefix"]
)
def test_delete_cleans_up_temporary_file_on_write_failure(monkeypatch, delete_func):
    import gptme.knowledge as knowledge

    entry = knowledge.knowledge_save("to delete", "resolution")
    kept_entry = knowledge.knowledge_save("keep me", "resolution")
    path = knowledge._entries_file()
    original_dump = knowledge.json.dumps

    def fail_for_persisted_entry(value):
        if isinstance(value, dict) and value.get("id") != entry["id"]:
            raise OSError("disk full")
        return original_dump(value)

    monkeypatch.setattr(knowledge.json, "dumps", fail_for_persisted_entry)
    entry_id = entry["id"] if delete_func == "knowledge_delete" else entry["id"][:8]

    with pytest.raises(OSError, match="disk full"):
        getattr(knowledge, delete_func)(entry_id)

    assert list(path.parent.glob("*.tmp")) == []
    assert knowledge._load_entries() == [entry, kept_entry]


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


def test_load_entries_tolerates_invalid_utf8():
    from gptme.knowledge import _entries_file, knowledge_list, knowledge_save

    entry = knowledge_save("valid problem", "valid resolution")
    with _entries_file().open("ab") as f:
        f.write(b"\xff\xfe not utf-8\n")

    assert knowledge_list() == [entry]


def test_search_matches_underscore_delimited_term():
    from gptme.knowledge import knowledge_save, knowledge_search

    entry = knowledge_save("foo_bar failure", "restart foo_bar")

    assert knowledge_search("foo") == [entry]
    assert knowledge_search("foo_bar") == [entry]
    assert knowledge_search("digit") == []


def test_search_does_not_match_embedded_substring():
    from gptme.knowledge import knowledge_save, knowledge_search

    knowledge_save("education catalog", "not a feline")

    assert knowledge_search("cat") == []


def test_search_and_list_when_lock_not_writable(monkeypatch):
    import gptme.knowledge as knowledge

    entry = knowledge.knowledge_save("readable problem", "readable resolution")

    def deny_lock():
        raise PermissionError("read-only knowledge directory")

    monkeypatch.setattr(knowledge, "_exclusive_lock", deny_lock)

    assert knowledge.knowledge_search("readable") == [entry]
    assert knowledge.knowledge_list() == [entry]


def test_search_and_list_strip_tag_filter():
    from gptme.knowledge import knowledge_list, knowledge_save, knowledge_search

    entry = knowledge_save("tagged problem", "tagged resolution", tags=["git"])

    assert knowledge_search("tagged", tags=[" git "]) == [entry]
    assert knowledge_list(tags=[" git "]) == [entry]


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
    from gptme.knowledge import _knowledge_dir, knowledge_save

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
        # Re-index is skipped when rag/ does not exist; create it so this
        # test still exercises the subprocess error path.
        (_knowledge_dir() / "rag").mkdir(parents=True)
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


def test_cli_json_output_escapes_control_characters():
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "knowledge",
            "save",
            "unsafe\x1b[2J problem",
            "unsafe\x07 resolution",
        ],
    )

    search_result = runner.invoke(main, ["knowledge", "search", "--json", "unsafe"])
    list_result = runner.invoke(main, ["knowledge", "list", "--json"])

    assert search_result.exit_code == 0, search_result.output
    assert list_result.exit_code == 0, list_result.output
    assert "\x1b" not in search_result.output
    assert "\x07" not in search_result.output
    assert "\x1b" not in list_result.output
    assert "\x07" not in list_result.output
    assert r"\u001b" in search_result.output
    assert r"\u0007" in search_result.output
    assert json.loads(search_result.output)[0]["problem"] == "unsafe\x1b[2J problem"


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


def test_cli_save_skips_index_when_mirror_export_fails(monkeypatch):
    monkeypatch.setattr("gptme.cli.cmd_knowledge.shutil.which", lambda _: "gptme-rag")

    def fail_export(*args, **kwargs):
        raise PermissionError("cannot write mirror")

    monkeypatch.setattr("gptme.cli.cmd_knowledge._export_for_rag", fail_export)
    calls = []
    monkeypatch.setattr(
        "gptme.cli.cmd_knowledge.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = CliRunner().invoke(
        main, ["knowledge", "save", "saved problem", "saved resolution"]
    )

    assert result.exit_code == 0, result.output
    assert "mirror export failed" in result.output
    assert calls == []


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


def test_cli_list_reports_unicode_error(monkeypatch):
    def fail(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")

    monkeypatch.setattr("gptme.knowledge.knowledge_list", fail)
    runner = CliRunner()

    result = runner.invoke(main, ["knowledge", "list"])

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert result.exception is None or not isinstance(
        result.exception, UnicodeDecodeError
    )


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


def test_cli_delete_strips_control_characters_from_prefix():
    runner = CliRunner()
    result = runner.invoke(main, ["knowledge", "delete", "missing\x1b[2Jprefix"])

    assert result.exit_code != 0
    assert "\x1b" not in result.output
    assert "No entry found" in result.output


def test_cli_delete_skips_reindex_when_rag_dir_missing(monkeypatch):
    from gptme.knowledge import knowledge_save

    entry = knowledge_save("delete me", "resolution")
    monkeypatch.setattr("gptme.cli.cmd_knowledge.shutil.which", lambda _: "gptme-rag")
    calls = []
    monkeypatch.setattr(
        "gptme.cli.cmd_knowledge.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = CliRunner().invoke(main, ["knowledge", "delete", entry["id"]])

    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output
    assert calls == []
    assert "re-index" not in result.output


# ---------------------------------------------------------------------------
# Session-start injection helpers
# ---------------------------------------------------------------------------


def test_select_knowledge_for_session_requires_two_keywords():
    from gptme.knowledge import knowledge_save, select_knowledge_for_session

    knowledge_save("pytest test discovery fails", "prefix test function with test_")

    assert select_knowledge_for_session(None) == []
    assert select_knowledge_for_session("") == []
    assert select_knowledge_for_session("hi") == []


def test_select_knowledge_for_session_matches_query():
    from gptme.knowledge import knowledge_save, select_knowledge_for_session

    knowledge_save("pytest test discovery fails", "prefix test function with test_")
    knowledge_save("git merge conflict resolution", "use git mergetool")

    results = select_knowledge_for_session("pytest discovery is broken")
    assert results
    assert results[0]["problem"] == "pytest test discovery fails"


def test_select_knowledge_for_session_swallows_oserror(monkeypatch):
    from gptme.knowledge import select_knowledge_for_session

    def boom(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr("gptme.knowledge.knowledge_search", boom)
    assert select_knowledge_for_session("pytest discovery fails") == []


def test_format_knowledge_prompt_empty():
    from gptme.knowledge import format_knowledge_prompt

    assert format_knowledge_prompt([]) == ""


def test_format_knowledge_prompt_includes_problem_resolution_and_tags():
    from gptme.knowledge import format_knowledge_prompt, knowledge_save

    entry = knowledge_save(
        "pytest test discovery fails",
        "prefix test function with test_",
        tags=["pytest"],
    )
    text = format_knowledge_prompt([entry])
    assert text.startswith("<knowledge-entries>")
    assert text.rstrip().endswith("</knowledge-entries>")
    assert "pytest test discovery fails" in text
    assert "prefix test function with test_" in text
    assert "tags: pytest" in text


def test_format_knowledge_prompt_clips_long_fields():
    from gptme.knowledge import format_knowledge_prompt, knowledge_save

    entry = knowledge_save("p" * 400, "r" * 400)
    text = format_knowledge_prompt([entry])
    assert "..." in text
    assert len(text) < 800


def test_format_knowledge_prompt_clips_long_tags():
    from gptme.knowledge import format_knowledge_prompt, knowledge_save

    entry = knowledge_save("short problem", "short resolution", tags=["x" * 400])
    text = format_knowledge_prompt([entry])
    tags_lines = [
        line for line in text.splitlines() if line.strip().startswith("tags:")
    ]
    assert tags_lines
    assert "..." in tags_lines[0]
    assert len(tags_lines[0]) < 260
