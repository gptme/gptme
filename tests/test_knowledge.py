"""Tests for the cross-session knowledge base (``gptme-util knowledge``).

Covers the storage layer (save/load/search) and the CLI subcommand dispatch.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_knowledge import knowledge
from gptme.knowledge import (
    KnowledgeValidationError,
    get_knowledge_file,
    load_entries,
    save_entry,
    search_entries,
)

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_kb(tmp_path, monkeypatch):
    """Point the knowledge file at a temp dir so tests don't touch live data."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return get_knowledge_file()


# ── storage layer ─────────────────────────────────────────────────────────────


def test_save_and_load(isolated_kb):
    save_entry(
        problem="pydantic_settings import fails",
        resolution="pip install pydantic-settings",
        problem_tags=["pydantic", "import"],
    )
    entries = load_entries()
    assert len(entries) == 1
    assert entries[0].problem == "pydantic_settings import fails"
    assert isolated_kb.exists()


def test_save_validation_required(isolated_kb):
    with pytest.raises(KnowledgeValidationError):
        save_entry(problem="", resolution="fix")


def test_save_validation_url_rejected(isolated_kb):
    with pytest.raises(KnowledgeValidationError):
        save_entry(problem="bad", resolution="see https://example.com/x")


def test_save_validation_max_problem(isolated_kb):
    with pytest.raises(KnowledgeValidationError):
        save_entry(problem="x" * 201, resolution="fix")


def test_load_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert load_entries() == []


def test_load_skips_corrupt_lines(isolated_kb):
    isolated_kb.parent.mkdir(parents=True, exist_ok=True)
    isolated_kb.write_text("{not json}\n")
    assert load_entries() == []


def test_load_skips_valid_json_wrong_schema(isolated_kb):
    """Valid JSON that is not a dict (or has wrong-type list fields) is skipped."""
    isolated_kb.parent.mkdir(parents=True, exist_ok=True)
    # JSON array at the top level — not a dict
    isolated_kb.write_text('["not", "an", "entry"]\n')
    assert load_entries() == []


def test_load_skips_wrong_type_list_fields(isolated_kb):
    """Records whose list fields are non-lists are skipped without crashing."""
    isolated_kb.parent.mkdir(parents=True, exist_ok=True)
    bad = {
        "id": "abc",
        "problem": "p",
        "resolution": "r",
        "problem_tags": "not-a-list",  # should be a list
        "keywords": 42,  # should be a list
        "context": "",
        "verified_at": "",
        "session_id": "",
        "model": "",
    }
    isolated_kb.write_text(json.dumps(bad) + "\n")
    # Should degrade gracefully — the record loads with empty lists, not a crash
    entries = load_entries()
    assert len(entries) == 1
    assert entries[0].problem_tags == []
    assert entries[0].keywords == []


def test_load_coerces_non_string_scalar_fields(isolated_kb):
    """Integer/None scalar fields are coerced to str instead of crashing."""
    isolated_kb.parent.mkdir(parents=True, exist_ok=True)
    bad: dict[str, object] = {
        "id": 42,  # int, not str
        "problem": None,  # None, not str
        "resolution": 3.14,  # float, not str
        "problem_tags": [],
        "keywords": [],
        "context": "",
        "verified_at": "",
        "session_id": "",
        "model": "",
    }
    isolated_kb.write_text(json.dumps(bad) + "\n")
    entries = load_entries()
    assert len(entries) == 1
    assert entries[0].id == "42"
    assert entries[0].problem == ""  # None → ""
    assert entries[0].resolution == "3.14"


def test_save_utf8_accepted(isolated_kb):
    """Printable non-ASCII UTF-8 text must be accepted."""
    entry = save_entry(
        problem="Résoudre l'erreur d'importation",
        resolution="pip install pydantic-settings",
    )
    assert entry.problem == "Résoudre l'erreur d'importation"
    entries = load_entries()
    assert entries[0].problem == "Résoudre l'erreur d'importation"


def test_search_unicode_terms(isolated_kb):
    """Non-ASCII search terms must match saved non-ASCII entries."""
    save_entry(problem="Résoudre erreur importation", resolution="pip install pkg")
    save_entry(problem="unrelated english", resolution="do nothing")
    results = search_entries("Résoudre")
    assert results
    assert results[0].problem == "Résoudre erreur importation"


def test_search_relevant_first(isolated_kb):
    save_entry(problem="pydantic import error", resolution="install pydantic-settings")
    save_entry(problem="unrelated theme", resolution="nothing to do here")
    results = search_entries("pydantic import", top_k=5)
    assert results
    assert results[0].problem == "pydantic import error"


def test_search_no_match(isolated_kb):
    save_entry(problem="pydantic error", resolution="install pydantic-settings")
    assert search_entries("zzzz_nomatch", top_k=5) == []


def test_search_empty_kb(isolated_kb):
    assert search_entries("anything") == []


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_save_and_search(isolated_kb):
    runner = CliRunner()
    result = runner.invoke(
        knowledge,
        ["save", "--problem", "import fails", "--resolution", "pip install pkg"],
    )
    assert result.exit_code == 0, result.output
    assert "Saved knowledge entry" in result.output

    result = runner.invoke(knowledge, ["search", "import fails"])
    assert result.exit_code == 0, result.output
    assert "import fails" in result.output


def test_cli_save_validation_error(isolated_kb):
    runner = CliRunner()
    result = runner.invoke(knowledge, ["save", "--problem", "", "--resolution", "x"])
    assert result.exit_code != 0
    assert "required" in result.output


def test_cli_search_json(isolated_kb):
    runner = CliRunner()
    runner.invoke(
        knowledge,
        ["save", "--problem", "unique prob", "--resolution", "unique res"],
    )
    result = runner.invoke(knowledge, ["search", "unique prob", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["problem"] == "unique prob"
