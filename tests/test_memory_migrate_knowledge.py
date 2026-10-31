"""Tests for ``gptme-util memory migrate-knowledge-jsonl``."""

import hashlib
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


@pytest.mark.parametrize("skip", [True, False])
def test_migrate_preserves_colliding_identities_and_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skip: bool
) -> None:
    rows = [
        {
            **_ENTRY_A,
            "id": f"aaaaaaaa-{i}",
            "problem": "same topic",
            "resolution": f"payload {i}",
        }
        for i in range(3)
    ]
    rows.append({**_ENTRY_B, "problem": "same-topic-aaaaaaaa"})
    source = _make_jsonl(tmp_path / "entries.jsonl", rows)
    before = source.read_bytes()
    root = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    args = ["memory", "migrate-knowledge-jsonl", str(source), "--scope", "explicit"]
    if not skip:
        args.append("--no-skip-existing")
    runner = CliRunner()
    dry = runner.invoke(util_main, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "would migrate 4, skipped 0" in dry.output
    assert not root.exists()
    first = runner.invoke(util_main, args)
    assert first.exit_code == 0, first.output
    store = MemoryStore([MemoryRoot("explicit", root)])
    saved = store.entries()
    assert len(saved) == 4
    by_id = {e.metadata["provenance"]["original_id"]: e for e in saved}
    names = {key: e.name for key, e in by_id.items()}
    for row in rows:
        entry = by_id[row["id"]]
        assert str(row["resolution"]) in entry.body
        assert (
            entry.metadata["provenance"]["migrated_from_created_at"]
            == row["created_at"]
        )
    # Reordering does not change which destination belongs to each source ID.
    _make_jsonl(source, list(reversed(rows)))
    again = runner.invoke(util_main, args)
    assert again.exit_code == 0, again.output
    assert ("skipped 4" if skip else "migrated 4") in again.output
    saved_again = store.entries()
    assert len(saved_again) == 4
    assert {
        e.metadata["provenance"]["original_id"]: e.name for e in saved_again
    } == names
    _make_jsonl(source, rows)
    assert source.read_bytes() == before


@pytest.mark.parametrize("scope", [None, "user", "project"])
def test_migrate_admission_uses_physical_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str | None
) -> None:
    from gptme.cli import cmd_memory

    target = MemoryRoot(scope or "project", tmp_path / "target")
    # Same scope can occur more than once (explicit directory lists).
    other = MemoryRoot(target.scope, tmp_path / "other")
    store = MemoryStore([target, other])
    MemoryStore([other]).save(
        "same-topic", "unrelated", "retain these bytes", scope=other.scope
    )
    unrelated = (other.path / "same-topic.md").read_bytes()
    monkeypatch.setattr(cmd_memory, "_store", lambda: store)
    source = _make_jsonl(
        tmp_path / "entries.jsonl", [{**_ENTRY_A, "problem": "same topic"}]
    )
    args = ["memory", "migrate-knowledge-jsonl", str(source)]
    if scope:
        args.extend(["--scope", scope])
    result = CliRunner().invoke(util_main, args)
    assert result.exit_code == 0, result.output
    assert "migrated 1, skipped 0" in result.output
    assert len(MemoryStore([target]).entries()) == 1
    assert (other.path / "same-topic.md").read_bytes() == unrelated


def test_migrate_missing_ids_use_stable_content_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {**_ENTRY_A, "id": "", "problem": "same topic", "resolution": f"payload {i}"}
        for i in range(3)
    ]
    source = _make_jsonl(tmp_path / "entries.jsonl", rows)
    root = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    args = ["memory", "migrate-knowledge-jsonl", str(source), "--scope", "explicit"]
    runner = CliRunner()
    assert runner.invoke(util_main, args).exit_code == 0
    store = MemoryStore([MemoryRoot("explicit", root)])
    before = {e.name: e.body for e in store.entries()}
    assert len(before) == 3
    _make_jsonl(source, list(reversed(rows)))
    result = runner.invoke(util_main, args)
    assert result.exit_code == 0, result.output
    assert "skipped 3" in result.output
    assert {e.name: e.body for e in store.entries()} == before


@pytest.mark.parametrize("skip", [True, False])
def test_migrate_existing_destination_belongs_to_another_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skip: bool
) -> None:
    root = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    store = MemoryStore([MemoryRoot("explicit", root)])
    old = {**_ENTRY_A, "problem": "same topic"}
    # Simulate an entry written by the pre-repair command (no migration_identity).
    path = store.save(
        "same-topic",
        "old migration",
        "original payload",
        scope="explicit",
        metadata={
            "provenance": {"source": "knowledge-jsonl", "original_id": old["id"]}
        },
    )
    before = path.read_bytes()
    new = {**_ENTRY_B, "problem": "same topic"}
    source = _make_jsonl(tmp_path / "entries.jsonl", [new])
    args = ["memory", "migrate-knowledge-jsonl", str(source), "--scope", "explicit"]
    if not skip:
        args.append("--no-skip-existing")
    result = CliRunner().invoke(util_main, args)
    assert result.exit_code == 0, result.output
    assert "migrated 1, skipped 0" in result.output
    assert len(store.entries()) == 2
    assert path.read_bytes() == before
    # Returning to the older ID must match its legacy provenance, not allocate a new file.
    _make_jsonl(source, [old])
    result = CliRunner().invoke(util_main, args)
    assert result.exit_code == 0, result.output
    assert ("skipped 1" if skip else "migrated 1") in result.output
    assert len(store.entries()) == 2


@pytest.mark.parametrize("skip", [True, False])
@pytest.mark.parametrize("invalid_utf8_first", [False, True])
def test_migrate_preserves_unparseable_occupied_destinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    skip: bool,
    invalid_utf8_first: bool,
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    row = {**_ENTRY_A, "problem": "same topic"}
    source = _make_jsonl(tmp_path / "entries.jsonl", [row])
    source_before = source.read_bytes()
    suffix = hashlib.sha256(f"id:{row['id']}".encode()).hexdigest()[:16]
    occupied = [root / "same-topic.md", root / f"same-topic-{suffix}.md"]
    payloads = [b"KEEP: no memory frontmatter\n", b"\xffKEEP BYTES\n"]
    if invalid_utf8_first:
        payloads.reverse()
    before = dict(zip(occupied, payloads, strict=True))
    for path, payload in before.items():
        path.write_bytes(payload)
    destination = root / f"same-topic-{suffix}-2.md"
    args = ["memory", "migrate-knowledge-jsonl", str(source), "--scope", "explicit"]
    if not skip:
        args.append("--no-skip-existing")
    runner = CliRunner()

    dry = runner.invoke(util_main, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "would migrate 1, skipped 0" in dry.output
    assert {p: p.read_bytes() for p in root.glob("*.md")} == before

    result = runner.invoke(util_main, args)
    assert result.exit_code == 0, result.output
    assert "migrated 1, skipped 0" in result.output
    assert {p: p.read_bytes() for p in occupied} == before
    store = MemoryStore([MemoryRoot("explicit", root)])
    entries = store.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == destination.stem
    assert destination.exists()
    assert str(row["problem"]) in entry.body
    assert str(row["resolution"]) in entry.body
    assert entry.metadata["provenance"]["original_id"] == row["id"]
    published = {p: p.read_bytes() for p in root.glob("*.md")}

    rerun = runner.invoke(util_main, args)
    assert rerun.exit_code == 0, rerun.output
    assert (
        "migrated 0, skipped 1" if skip else "migrated 1, skipped 0"
    ) in rerun.output
    assert {p: p.read_bytes() for p in root.glob("*.md")} == published
    assert source.read_bytes() == source_before


@pytest.mark.parametrize("scope", [None, "user", "project"])
def test_migrate_unrelated_layer_does_not_suppress_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str | None
) -> None:
    from gptme.cli import cmd_memory

    project = MemoryRoot("project", tmp_path / "project")
    user = MemoryRoot("user", tmp_path / "user")
    target = user if scope == "user" else project
    other = project if scope == "user" else user
    MemoryStore([other]).save("same-topic", "unrelated", "retain", scope=other.scope)
    before = (other.path / "same-topic.md").read_bytes()
    store = MemoryStore([project, user])
    monkeypatch.setattr(cmd_memory, "_store", lambda: store)
    source = _make_jsonl(
        tmp_path / "entries.jsonl", [{**_ENTRY_A, "problem": "same topic"}]
    )
    args = ["memory", "migrate-knowledge-jsonl", str(source)]
    if scope:
        args.extend(["--scope", scope])
    result = CliRunner().invoke(util_main, args)
    assert result.exit_code == 0, result.output
    assert "migrated 1, skipped 0" in result.output
    assert len(MemoryStore([target]).entries()) == 1
    assert (other.path / "same-topic.md").read_bytes() == before


def test_migrate_inventory_preserves_duplicate_frontmatter_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    store = MemoryStore([MemoryRoot("explicit", root)])
    store.save("aaa", "unrelated", "KEEP A")
    hidden = store.save(
        "same-topic",
        "legacy",
        "KEEP B",
        metadata={"provenance": {"source": "knowledge-jsonl", "original_id": "B"}},
    )
    hidden.write_text(hidden.read_text().replace('name: "same-topic"', 'name: "aaa"'))
    before = hidden.read_bytes()
    source = _make_jsonl(
        tmp_path / "entries.jsonl", [{**_ENTRY_A, "problem": "same topic"}]
    )
    result = CliRunner().invoke(
        util_main, ["memory", "migrate-knowledge-jsonl", str(source)]
    )
    assert result.exit_code == 0, result.output
    assert "migrated 1, skipped 0" in result.output
    assert hidden.read_bytes() == before
    assert len(list(root.glob("same-topic*.md"))) == 2


@pytest.mark.parametrize(
    "provenance",
    [None, "manual note", [], {"source": "knowledge-jsonl", "migration_identity": []}],
)
def test_migrate_unrelated_provenance_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: object
) -> None:
    root = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    store = MemoryStore([MemoryRoot("explicit", root)])
    path = store.save("other", "unrelated", "KEEP", metadata={"provenance": provenance})
    before = path.read_bytes()
    source = _make_jsonl(tmp_path / "entries.jsonl", [_ENTRY_A])
    result = CliRunner().invoke(
        util_main, ["memory", "migrate-knowledge-jsonl", str(source)]
    )
    assert result.exit_code == 0, result.output
    assert "migrated 1" in result.output
    assert path.read_bytes() == before


def test_migrate_repeated_source_identity_dry_run_matches_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    source = _make_jsonl(tmp_path / "entries.jsonl", [_ENTRY_A, _ENTRY_A])
    before = source.read_bytes()
    args = ["memory", "migrate-knowledge-jsonl", str(source)]
    runner = CliRunner()
    dry = runner.invoke(util_main, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "would migrate 1, skipped 1" in dry.output
    result = runner.invoke(util_main, args)
    assert result.exit_code == 0, result.output
    assert "migrated 1, skipped 1" in result.output
    assert source.read_bytes() == before
    assert len(MemoryStore([MemoryRoot("explicit", root)]).entries()) == 1


@pytest.mark.parametrize("dry", [False, True])
def test_migrate_refuses_normalization_of_recovered_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry: bool
) -> None:
    root = tmp_path / "memory"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    store = MemoryStore([MemoryRoot("explicit", root)])
    first = store.save(
        "same-topic",
        "A",
        "KEEP A",
        metadata={"provenance": {"source": "knowledge-jsonl", "original_id": "A"}},
    )
    renamed = root / "same_topic.md"
    first.rename(renamed)
    second = store.save(
        "same-topic",
        "B",
        "KEEP B",
        metadata={"provenance": {"source": "knowledge-jsonl", "original_id": "B"}},
    )
    before = {p.name: p.read_bytes() for p in (renamed, second)}
    source = _make_jsonl(tmp_path / "entries.jsonl", [{**_ENTRY_A, "id": "A"}])
    args = ["memory", "migrate-knowledge-jsonl", str(source), "--no-skip-existing"]
    if dry:
        args.append("--dry-run")
    result = CliRunner().invoke(util_main, args)
    assert result.exit_code == 1, result.output
    assert "noncanonical destination" in result.output
    assert {p.name: p.read_bytes() for p in (renamed, second)} == before
