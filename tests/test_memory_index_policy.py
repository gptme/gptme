"""Persistent index selection must survive every writer without losing recall."""

import json
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, cast

import pytest
from click.testing import CliRunner

from gptme.cli.util import main as util_main
from gptme.memory import (
    MemoryEntry,
    MemoryFrontmatterError,
    MemoryRoot,
    MemoryStore,
    parse_entry,
    recall,
)
from gptme.memory.store import LOCK_FILENAME


def _store(root: Path) -> MemoryStore:
    root.mkdir(parents=True, exist_ok=True)
    return MemoryStore([MemoryRoot("explicit", root)])


def _policy(root: Path, selected: list[str], budget: int = 4096) -> Path:
    path = root / ".memory-index.json"
    path.write_text(
        json.dumps({"version": 1, "budget": budget, "selected": selected}),
        encoding="utf-8",
    )
    return path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in root.iterdir()
        if path.suffix in {".md", ".json"}
    }


def test_selected_entries_survive_regeneration_and_new_save(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("z-first", "Curated operator description", title="Operator guidance")
    store.save("a-second", "Second selected memory")
    store.save("recall-only", "Neutrino oscillation experiment", "Detector calibration")
    policy = _policy(tmp_path, ["z-first.md", "a-second.md"])
    before_policy = policy.read_bytes()

    expected = store.render_root_index()
    assert "[Operator guidance](z-first.md) — Curated operator description" in expected
    assert "a-second.md" in expected
    assert "recall-only.md" not in expected
    for _ in range(2):
        store.write_index()
        assert store.check_index()
        assert (tmp_path / "MEMORY.md").read_text() == expected
    store.save("new-memory", "Another neutrino experiment", "New evidence")
    assert (tmp_path / "MEMORY.md").read_text() == expected
    assert policy.read_bytes() == before_policy
    assert store.check_index()
    assert parse_entry(tmp_path / "recall-only.md").is_living
    result = recall(store, "neutrino experiment", backend="overlap", limit=5)
    assert {hit.entry.name for hit in result.hits} == {"recall-only", "new-memory"}


def test_empty_selection_does_not_promote_new_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _policy(tmp_path, [])
    store.write_index()
    before = (tmp_path / "MEMORY.md").read_bytes()
    store.save("new", "Useful but not always-on")
    assert (tmp_path / "MEMORY.md").read_bytes() == before
    assert store.check_index()
    assert len(store.entries()) == 1


def test_save_preserves_unspecified_fields_under_policy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = MemoryEntry(
        name="kept",
        description="Original",
        title="Curated title",
        type="feedback",
        provenance={"session": "source-session"},
        metadata={"originSessionId": "original", "custom": ["a", "b"]},
        keywords=["stable guidance"],
        confidence=0.8,
        recheck="2026-10-01",
        body="Old body",
    )
    path = tmp_path / "kept.md"
    path.write_text(entry.to_markdown(), encoding="utf-8")
    _policy(tmp_path, ["kept.md"])
    store.save("kept", "Updated description", "New body")
    updated = parse_entry(path)
    original_fields = entry.to_dict()
    updated_fields = updated.to_dict()
    for key in ("description", "body", "path", "scope"):
        original_fields.pop(key, None)
        updated_fields.pop(key, None)
    assert updated_fields == original_fields
    assert updated.description == "Updated description"
    assert updated.body == "New body"
    assert store.check_index()
    assert "Curated title" in (tmp_path / "MEMORY.md").read_text()

    store.save(
        "kept",
        "Explicit replacements",
        "Body",
        type="project",
        title="New title",
        metadata={"custom": "new"},
    )
    replaced = parse_entry(path)
    assert replaced.type == "project"
    assert replaced.title == "New title"
    assert replaced.metadata == {"custom": "new", "originSessionId": "original"}
    assert replaced.provenance == entry.provenance


def test_save_does_not_resurrect_unselected_superseded_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("old", "Old belief")
    store.save("new", "Replacement")
    _policy(tmp_path, ["new.md"])
    store.supersede("old", "new")
    before = parse_entry(tmp_path / "old.md")
    store.save("old", "Historical clarification", "Updated old body")
    after = parse_entry(tmp_path / "old.md")
    assert after.status == before.status == "superseded"
    assert after.superseded_by == before.superseded_by == "new"
    assert "old.md" not in (tmp_path / "MEMORY.md").read_text()
    assert store.check_index()


def test_utf8_overflow_save_is_atomic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("kept", "a")
    full = store.render_index(store.entries())
    budget = len(full.encode("utf-8"))
    _policy(tmp_path, ["kept.md"], budget)
    store.write_index()
    before = _snapshot(tmp_path)
    # Equal character count, but the replacement requires more UTF-8 bytes.
    with pytest.raises(ValueError, match="budget"):
        store.save("kept", "界", "Changed body")
    assert _snapshot(tmp_path) == before
    assert store.check_index()


@pytest.mark.parametrize(
    "selected", [["old.md", "tail.md"], ["old.md", "tail.md", "new.md"]]
)
def test_supersede_transfers_selection_and_is_idempotent(
    tmp_path: Path, selected: list[str]
) -> None:
    store = _store(tmp_path)
    for name in ("old", "new", "tail"):
        store.save(name, name)
    policy = _policy(tmp_path, selected)
    store.write_index()
    store.supersede("old", "new")
    assert json.loads(policy.read_text())["selected"] == ["new.md", "tail.md"]
    text = (tmp_path / "MEMORY.md").read_text()
    assert text.count("(new.md)") == 1
    assert "old.md" not in text
    assert "tail.md" in text
    assert store.check_index()
    after = _snapshot(tmp_path)
    store.supersede("old", "new")
    assert _snapshot(tmp_path) == after


def test_supersede_budget_overflow_rolls_back_policy_entries_and_index(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.save("old", "short")
    store.save("new", "界" * 200)
    _policy(tmp_path, ["old.md"], 120)
    store.write_index()
    before = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="budget"):
        store.supersede("old", "new")
    assert _snapshot(tmp_path) == before
    assert parse_entry(tmp_path / "old.md").is_living
    assert store.check_index()


@pytest.mark.parametrize(
    "raw",
    [
        "{not json}",
        "[]",
        '{"version": 2, "budget": 4096, "selected": ["kept.md"]}',
        '{"version": true, "budget": 4096, "selected": ["kept.md"]}',
        '{"version": 1, "budget": true, "selected": ["kept.md"]}',
        '{"version": 1, "budget": 0, "selected": ["kept.md"]}',
        '{"version": 1, "budget": 1.5, "selected": ["kept.md"]}',
        '{"version": 1, "budget": 4096, "selected": "kept.md"}',
        '{"version": 1, "budget": 4096, "selected": [5]}',
        '{"version": 1, "budget": 4096, "selected": ["../kept.md"]}',
        '{"version": 1, "budget": 4096, "selected": ["kept.md", "kept.md"]}',
        '{"version": 1, "budget": 4096}',
    ],
)
def test_malformed_policy_fails_closed(tmp_path: Path, raw: str) -> None:
    store = _store(tmp_path)
    store.save("kept", "Original")
    store.save("replacement", "Replacement")
    (tmp_path / ".memory-index.json").write_text(raw, encoding="utf-8")
    before = _snapshot(tmp_path)
    for operation in (
        store.render_root_index,
        store.write_index,
        store.check_index,
        lambda: store.save("kept", "Changed"),
        lambda: store.supersede("kept", "replacement"),
    ):
        with pytest.raises(ValueError, match="policy|memory-index|selected memory"):
            operation()
        assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("target", ["missing.md", "historical.md"])
def test_selected_target_must_exist_and_be_living(tmp_path: Path, target: str) -> None:
    store = _store(tmp_path)
    store.save("kept", "Original")
    historical = MemoryEntry(
        name="historical", description="Archive", status="historical"
    )
    (tmp_path / "historical.md").write_text(historical.to_markdown(), encoding="utf-8")
    _policy(tmp_path, ["kept.md", target])
    before = _snapshot(tmp_path)
    for operation in (
        store.render_root_index,
        store.write_index,
        store.check_index,
        lambda: store.save("new", "new"),
    ):
        with pytest.raises(ValueError, match="policy|memory-index|selected memory"):
            operation()
        assert _snapshot(tmp_path) == before


def test_cli_print_write_check_preserve_policy_and_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.save("kept", "a")
    store.save("unselected", "A living recall-only memory")
    budget = len(
        store.render_index([parse_entry(tmp_path / "kept.md")]).encode("utf-8")
    )
    _policy(tmp_path, ["kept.md"], budget)
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    runner = CliRunner()
    printed = runner.invoke(util_main, ["memory", "index"])
    assert printed.exit_code == 0, printed.output
    assert "kept.md" in printed.output
    assert "unselected.md" not in printed.output
    assert len(printed.output.encode("utf-8")) <= budget
    for flags in (["--write"], ["--check"], ["--write"], ["--check"]):
        result = runner.invoke(util_main, ["memory", "index", *flags])
        assert result.exit_code == 0, result.output
    assert (tmp_path / "MEMORY.md").read_text() == printed.output

    # A command-line budget may tighten the cap, but must never override it.
    before = _snapshot(tmp_path)
    for flags in ([], ["--write"], ["--check"]):
        result = runner.invoke(
            util_main, ["memory", "index", "--budget", str(budget - 1), *flags]
        )
        assert result.exit_code != 0
    assert _snapshot(tmp_path) == before

    kept = parse_entry(tmp_path / "kept.md")
    kept.description = "界" * 300
    (tmp_path / "kept.md").write_text(kept.to_markdown(), encoding="utf-8")
    before = _snapshot(tmp_path)
    for flags in ([], ["--write"], ["--check"]):
        result = runner.invoke(
            util_main, ["memory", "index", "--budget", "10000", *flags]
        )
        assert result.exit_code != 0
    assert _snapshot(tmp_path) == before


def test_legacy_save_preserves_curated_index_without_policy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("kept", "Original")
    index = tmp_path / "MEMORY.md"
    index.write_text(
        "# Operator context\n\nKeep this prose.\n\n- [kept](kept.md) — Original\n",
        encoding="utf-8",
    )
    store.save("new", "New memory")
    assert "Keep this prose." in index.read_text()
    assert "kept.md" in index.read_text()
    assert "new.md" in index.read_text()


def test_compatibility_index_helper_obeys_selection_and_cap(tmp_path: Path) -> None:
    from gptme.memory.store import update_index_line

    store = _store(tmp_path)
    store.save("kept", "Short guidance")
    store.save("recall-only", "Only recall this")
    _policy(tmp_path, ["kept.md"], 120)
    store.write_index()
    expected = (tmp_path / "MEMORY.md").read_bytes()
    update_index_line(tmp_path, parse_entry(tmp_path / "recall-only.md"))
    assert (tmp_path / "MEMORY.md").read_bytes() == expected
    assert store.check_index()
    oversized = parse_entry(tmp_path / "kept.md")
    oversized.description = "界" * 200
    (tmp_path / "kept.md").write_text(oversized.to_markdown(), encoding="utf-8")
    with pytest.raises(ValueError, match="budget"):
        update_index_line(tmp_path, oversized)
    assert (tmp_path / "MEMORY.md").read_bytes() == expected


def test_supersede_io_failure_restores_policy_and_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    store = _store(tmp_path)
    store.save("old", "Old")
    store.save("new", "New")
    _policy(tmp_path, ["old.md"])
    store.write_index()
    before = _snapshot(tmp_path)
    real_replace = os.replace
    destinations = []

    def fail_last_replace(src: Path, dest: Path) -> None:
        # Fail the final index publication after entry/policy replacements.
        if dest.name == "MEMORY.md":
            raise OSError("index publication failed")
        destinations.append(dest.name)
        real_replace(src, dest)

    monkeypatch.setattr("gptme.memory.store.os.replace", fail_last_replace)
    with pytest.raises(OSError, match="index publication failed"):
        store.supersede("old", "new")
    assert "old.md" in destinations
    assert "new.md" in destinations
    assert ".memory-index.json" in destinations
    assert _snapshot(tmp_path) == before
    assert store.check_index()


def test_policy_selection_uses_filename_even_when_entry_names_duplicate(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for filename, description in (("a.md", "Unselected"), ("z.md", "Selected")):
        entry = MemoryEntry(name="duplicate-name", description=description)
        (tmp_path / filename).write_text(entry.to_markdown(), encoding="utf-8")
    _policy(tmp_path, ["z.md"])
    # save() currently sees the just-written entry even if name-based reading
    # hides it. A successful write must remain regenerable afterwards.
    store.save("z", "Updated selected description")
    assert store.check_index()
    assert "(z.md)" in store.render_root_index()
    assert "(a.md)" not in store.render_root_index()


@pytest.mark.parametrize("operation", ["render_root_index", "check_index"])
def test_managed_reader_waits_for_supersede_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    store = _store(tmp_path)
    store.save("old", "Old")
    store.save("new", "New")
    _policy(tmp_path, ["old.md"])
    store.write_index()
    old_replaced = threading.Event()
    release_writer = threading.Event()
    reader_started = threading.Event()
    real_replace = os.replace

    def pause_after_old(src: Path, dest: Path) -> None:
        real_replace(src, dest)
        if dest.name == "old.md":
            old_replaced.set()
            assert release_writer.wait(timeout=5)

    def read() -> str | bool:
        reader_started.set()
        return getattr(store, operation)()

    monkeypatch.setattr("gptme.memory.store.os.replace", pause_after_old)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(store.supersede, "old", "new")
        try:
            assert old_replaced.wait(timeout=5)
            reader = pool.submit(read)
            assert reader_started.wait(timeout=5)
            # At this point old.md is superseded but the policy still selects it.
            # Readers must wait rather than report an invalid selected target.
            with pytest.raises(FutureTimeout):
                reader.result(timeout=0.1)
        finally:
            release_writer.set()
        writer.result(timeout=5)
        result = reader.result(timeout=5)
    if operation == "check_index":
        assert result is True
    else:
        assert isinstance(result, str)
        assert "(new.md)" in result
        assert "(old.md)" not in result


def test_managed_save_index_matches_serialized_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("kept", "Original")
    _policy(tmp_path, ["kept.md"])
    store.save("kept", "line one\nline two", title="Title\ncontinuation")
    assert store.check_index()
    assert (tmp_path / "MEMORY.md").read_text() == store.render_root_index()


def test_managed_supersede_index_matches_serialized_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("old", "Original")
    (tmp_path / "new.md").write_text(
        '---\nname: new\ndescription: "line one\\nline two"\n'
        'title: "Title\\ncontinuation"\n---\nNew body\n',
        encoding="utf-8",
    )
    assert "\n" in parse_entry(tmp_path / "new.md").description
    _policy(tmp_path, ["old.md"])
    store.supersede("old", "new")
    assert store.check_index()
    assert (tmp_path / "MEMORY.md").read_text() == store.render_root_index()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_managed_save_rolls_back_readonly_entry_after_index_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, newline: bytes
) -> None:
    import os
    import stat

    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("requires POSIX file permissions and an unprivileged user")
    store = _store(tmp_path)
    entry = store.save("kept", "Original description", "Original body")
    entry.write_bytes(entry.read_bytes().replace(b"\n", newline))
    _policy(tmp_path, ["kept.md"])
    store.write_index()
    entry.chmod(0o444)
    before = _snapshot(tmp_path)
    real_replace = os.replace

    def fail_index_replace(src: Path, dest: Path) -> None:
        if dest.name == "MEMORY.md":
            raise OSError("index publication failed")
        real_replace(src, dest)

    monkeypatch.setattr("gptme.memory.store.os.replace", fail_index_replace)
    try:
        with pytest.raises(
            OSError, match="index publication failed|rollback incomplete"
        ):
            store.save("kept", "Changed description", "Changed body")
        assert _snapshot(tmp_path) == before
        assert stat.S_IMODE(entry.stat().st_mode) == 0o444
    finally:
        entry.chmod(0o600)


def test_staging_failure_removes_partial_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    store = _store(tmp_path)
    _policy(tmp_path, [])
    store.write_index()
    before = _snapshot(tmp_path)
    real_fdopen = os.fdopen
    opened = 0

    class FailingWriter(AbstractContextManager["FailingWriter"]):
        def __init__(self, wrapped: BinaryIO) -> None:
            self.wrapped = wrapped

        def __enter__(self) -> "FailingWriter":
            self.wrapped.__enter__()
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            return self.wrapped.__exit__(exc_type, exc_value, traceback)

        def write(self, data: bytes) -> int:
            self.wrapped.write(data[:1])
            raise OSError("staging failed")

    def fail_second_write(fd: int, mode: str) -> BinaryIO | FailingWriter:
        nonlocal opened
        opened += 1
        wrapped = cast(BinaryIO, real_fdopen(fd, mode))
        return FailingWriter(wrapped) if opened == 2 else wrapped

    monkeypatch.setattr("gptme.memory.store.os.fdopen", fail_second_write)
    with pytest.raises(OSError, match="staging failed"):
        store.save("new", "Description")

    assert _snapshot(tmp_path) == before
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("managed", [False, True])
def test_save_rejects_malformed_existing_yaml_without_losing_metadata(
    tmp_path: Path, managed: bool
) -> None:
    store = _store(tmp_path)
    store.save("kept", "Original")
    path = tmp_path / "kept.md"
    path.write_text(
        "---\nname: kept\ndescription: unquoted: colon\n"
        "metadata:\n  type: feedback\n  originSessionId: source-session\n"
        "status: historical\nprovenance:\n  session: source-session\n"
        "---\nOriginal historical body\n",
        encoding="utf-8",
    )
    if managed:
        _policy(tmp_path, [])
        store.write_index()
    # Compatibility reads remain lenient; mutation must refuse so that lifecycle
    # fields (supersedes, keywords, provenance) are never silently discarded
    # by the lossy _lenient_load parser. The user must fix the frontmatter
    # with `gptme memory audit` before re-saving.
    readable = store.get("kept")
    assert readable is not None
    assert readable.status == "historical"
    assert readable.metadata["originSessionId"] == "source-session"
    before = _snapshot(tmp_path)
    with pytest.raises(MemoryFrontmatterError, match="invalid YAML"):
        store.save("kept", "Replacement description", "Replacement body")
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("managed", [False, True])
def test_save_rejects_existing_non_entry_without_traceback(
    tmp_path: Path, managed: bool
) -> None:
    store = _store(tmp_path)
    path = tmp_path / "kept.md"
    path.write_text("A stray note without frontmatter.\n", encoding="utf-8")
    if managed:
        _policy(tmp_path, [])
        store.write_index()
    before = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="frontmatter"):
        store.save("kept", "Replacement description", "Replacement body")

    assert _snapshot(tmp_path) == before


def test_render_and_check_on_readonly_root(tmp_path: Path) -> None:
    """render_root_index and check_index must not crash on a read-only root.

    Regression for the P1 found in AI review: _locked_root unconditionally
    creates the root directory and .memory.lock file, so any read-only root
    raised OSError before any data was read.
    """
    import stat

    store = _store(tmp_path)
    store.save("entry", "Description")
    _policy(tmp_path, ["entry.md"])
    store.write_index()

    # Remove the pre-existing lock file before restricting permissions.
    # Without this step the existing lock file is still owner-writable and
    # ``open(lock_file, "a+b")`` succeeds even when the directory is
    # read-only, so the OSError fallback in render_root_index/check_index
    # is never reached.  Deleting the file first forces _locked_root to
    # attempt creation, which fails on a read-only directory.
    (tmp_path / LOCK_FILENAME).unlink(missing_ok=True)

    # Make the root read-only so lock-file creation fails.
    tmp_path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        # Neither call must raise; they fall back to an unlocked read.
        text = store.render_root_index()
        assert "Description" in text

        ok = store.check_index()
        assert ok
    finally:
        # Restore write permission so pytest can clean up tmp_path.
        tmp_path.chmod(stat.S_IRWXU)


def test_policy_rejects_dot_prefixed_filenames(tmp_path: Path) -> None:
    """IndexPolicy.read must reject dot-prefixed filenames.

    glob("*.md") does not match hidden files, so a policy that selected
    ".secret.md" would make every managed operation raise ValueError because
    the selected entry is never found in the entries list.
    """
    import pytest

    _policy(tmp_path, [".hidden.md"])
    with pytest.raises(ValueError, match="local entry filenames"):
        from gptme.memory.policy import IndexPolicy

        IndexPolicy.read(tmp_path)


def test_policy_rejects_budget_below_minimum(tmp_path: Path) -> None:
    """IndexPolicy.read must reject budgets too small for the index header.

    A budget below the header size makes every index operation fail with a
    'selected memory index needs N bytes, exceeds budget M' error, rendering
    the root unusable until the policy file is hand-edited.
    """
    import pytest

    from gptme.memory.policy import POLICY_MIN_BUDGET, IndexPolicy

    # Verify the constant matches the actual rendered byte count for an empty
    # selection so the constant and the render path can't diverge silently.
    store = _store(tmp_path)
    policy = IndexPolicy(budget=POLICY_MIN_BUDGET, selected=())
    actual_min = len(store._render_with_policy([], policy).encode("utf-8"))
    assert actual_min == POLICY_MIN_BUDGET, (
        f"POLICY_MIN_BUDGET ({POLICY_MIN_BUDGET}) does not match the actual "
        f"rendered byte count for an empty selection ({actual_min})"
    )

    _policy(tmp_path, [], budget=POLICY_MIN_BUDGET - 1)
    with pytest.raises(ValueError, match="at least"):
        IndexPolicy.read(tmp_path)

    # Exactly at the minimum must be accepted.
    _policy(tmp_path, [], budget=POLICY_MIN_BUDGET)
    assert IndexPolicy.read(tmp_path) is not None
