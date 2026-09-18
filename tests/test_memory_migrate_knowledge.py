"""Tests for ``gptme-util memory migrate-knowledge-jsonl``."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_knowledge import knowledge
from gptme.cli.util import main as util_main
from gptme.memory import MemoryRoot, MemoryStore


@pytest.fixture(autouse=True)
def _clear_data_dir_cache():
    """Keep XDG_DATA_HOME changes visible despite get_data_dir's lru_cache."""
    from gptme import dirs

    if hasattr(dirs.get_data_dir, "cache_clear"):
        dirs.get_data_dir.cache_clear()
    yield
    if hasattr(dirs.get_data_dir, "cache_clear"):
        dirs.get_data_dir.cache_clear()


def _make_jsonl(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_ENTRY_A = {
    "id": "aaaaaaaa-0000-0000-0000-000000000001",
    "problem": "pytest discovers no tests despite test file existing",
    "resolution": "The test function was not prefixed with test_; rename it.",
    "tags": ["pytest", "testing"],
    "keywords": ["pytest discovers"],
    "created_at": "2026-01-01T00:00:00+00:00",
    "memory_type": "knowledge_entry",
}

_ENTRY_B = {
    "id": "bbbbbbbb-0000-0000-0000-000000000002",
    "problem": "git push rejected with non-fast-forward error",
    "resolution": "Pull first with git pull --rebase, then push.",
    "tags": ["git"],
    "keywords": [],
    "created_at": "2026-02-01T00:00:00+00:00",
    "memory_type": "knowledge_entry",
}


def test_migrate_basic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entries migrate into the memory store with correct fields."""
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [_ENTRY_A, _ENTRY_B])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert result.exit_code == 0, result.output
    assert "migrated: " in result.output
    assert "migrated 2" in result.output

    store = MemoryStore([MemoryRoot("explicit", mem_dir)])
    entries = store.entries()
    assert len(entries) == 2

    names = {e.name for e in entries}
    assert any("pytest-discovers" in n for n in names)
    assert any("git-push" in n for n in names)

    # Tags merged into keywords
    pytest_entry = next(e for e in entries if "pytest-discovers" in e.name)
    assert "pytest" in pytest_entry.keywords
    assert "testing" in pytest_entry.keywords
    assert "pytest discovers" in pytest_entry.keywords

    # Provenance recorded
    assert (
        pytest_entry.metadata.get("provenance", {}).get("source") == "knowledge-jsonl"
    )
    assert (
        pytest_entry.metadata["provenance"]["original_id"]
        == "aaaaaaaa-0000-0000-0000-000000000001"
    )

    # Body contains both sections
    assert "## Problem" in pytest_entry.body
    assert "## Resolution" in pytest_entry.body
    assert "prefixed with test_" in pytest_entry.body


def test_migrate_scalar_tags_stay_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scalar string tag/keyword is kept as one value, not split per character."""
    entry = {**_ENTRY_A, "tags": "git", "keywords": "non-fast-forward"}
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [entry])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert result.exit_code == 0, result.output

    store = MemoryStore([MemoryRoot("explicit", mem_dir)])
    migrated = store.entries()[0]
    assert "git" in migrated.keywords
    assert "non-fast-forward" in migrated.keywords
    # Guard against the per-character regression (str is iterable)
    assert "g" not in migrated.keywords
    assert "i" not in migrated.keywords


def test_migrate_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry-run shows what would migrate but writes nothing."""
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [_ENTRY_A])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "would migrate" in result.output
    assert not mem_dir.exists() or not list(mem_dir.glob("*.md"))


def test_migrate_dry_run_reports_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dry-run correctly reports would-skip for already-migrated entries."""
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [_ENTRY_A])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    runner = CliRunner()
    # First run — actually migrate
    r1 = runner.invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert r1.exit_code == 0, r1.output

    # Dry-run on same source — should report would-skip, not would-migrate
    r2 = runner.invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--dry-run"],
    )
    assert r2.exit_code == 0, r2.output
    # The per-entry line goes to stderr (err=True); the summary goes to stdout.
    assert "would skip" in r2.stderr
    assert "  would migrate:" not in r2.stdout


def test_migrate_skip_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entries already in the store are skipped by default."""
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [_ENTRY_A])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    # First migration
    runner = CliRunner()
    r1 = runner.invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert r1.exit_code == 0, r1.output
    assert "migrated 1" in r1.output

    # Second migration — should skip
    r2 = runner.invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert r2.exit_code == 0, r2.output
    assert "skipped 1" in r2.output
    assert "migrated 0" in r2.output


def test_migrate_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing JSONL exits 0 with an informational message (nothing to do)."""
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(tmp_path / "nonexistent.jsonl")],
    )
    # click.Path(exists=True) makes this exit 2 before our code runs
    assert result.exit_code == 2


def test_migrate_default_path_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default path (no arg) exits 0 cleanly when no knowledge store exists."""
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))
    # Point XDG_DATA_HOME to an empty tmp so there is no entries.jsonl
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

    result = CliRunner().invoke(util_main, ["memory", "migrate-knowledge-jsonl"])
    assert result.exit_code == 0
    assert (
        "nothing to migrate" in result.output.lower()
        or "No knowledge store" in result.output
    )


def test_migrate_preserves_entry_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed knowledge entries keep type-aware headings and provenance."""
    entry = {
        **_ENTRY_A,
        "problem": "Switched auth library from X to Y",
        "resolution": "X had no async support; Y supports both sync and async callers.",
        "entry_type": "decision",
    }
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [entry])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert result.exit_code == 0, result.output

    store = MemoryStore([MemoryRoot("explicit", mem_dir)])
    entries = store.entries()
    assert len(entries) == 1
    saved = entries[0]
    assert "## Decision" in saved.body
    assert "## Rationale" in saved.body
    assert saved.metadata.get("provenance", {}).get("original_entry_type") == "decision"


def test_migrate_slug_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entries with identical problem prefixes get distinct slugs via ID suffix."""
    # Two entries whose first 80 chars of problem text are identical
    long_prefix = "a" * 80
    entry_c = {**_ENTRY_A, "problem": long_prefix + " extra-c", "id": "cccccccc-0000"}
    entry_d = {**_ENTRY_B, "problem": long_prefix + " extra-d", "id": "dddddddd-0000"}
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [entry_c, entry_d])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert result.exit_code == 0, result.output
    assert "migrated 2" in result.output

    store = MemoryStore([MemoryRoot("explicit", mem_dir)])
    names = {e.name for e in store.entries()}
    assert len(names) == 2, f"Expected 2 distinct names, got: {names}"


def test_migrate_malformed_tags_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A null tags/keywords field is tolerated; migration continues for valid records."""
    entry_bad = {**_ENTRY_A, "tags": None, "keywords": None}
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [entry_bad, _ENTRY_B])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert result.exit_code == 0, result.output
    assert "migrated 2" in result.output


def test_migrate_malformed_entry_type_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-string entry_type falls back to the default instead of aborting migration."""
    entry_bad = {**_ENTRY_A, "entry_type": ["decision"]}
    jsonl = _make_jsonl(tmp_path / "entries.jsonl", [entry_bad, _ENTRY_B])
    mem_dir = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(mem_dir))

    result = CliRunner().invoke(
        util_main,
        ["memory", "migrate-knowledge-jsonl", str(jsonl), "--scope", "explicit"],
    )
    assert result.exit_code == 0, result.output
    assert "migrated 2" in result.output


def test_knowledge_deprecation_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``gptme-util knowledge`` commands emit a deprecation notice on stderr."""
    _make_jsonl(
        tmp_path / "knowledge" / "entries.jsonl",
        [_ENTRY_A],
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    result = CliRunner().invoke(
        knowledge,
        ["list"],
        catch_exceptions=False,
    )
    assert "deprecated" in result.stderr.lower()
