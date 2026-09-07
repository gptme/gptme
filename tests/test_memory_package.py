"""
Tests for gptme.memory — the unified cross-runtime memory package.

Covers:
- Schema round-trip (parse → write → parse)
- CC-format byte-compatibility
- Root resolution
- save / list / show / index operations
- supersede
- CLI subcommands via Click test runner
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.memory.ops import (
    index_memory,
    list_entries,
    save_memory,
    show_entry,
    supersede,
)
from gptme.memory.roots import resolve_roots, write_root
from gptme.memory.schema import MemoryEntry, parse_entry, write_entry

# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------


class TestSchemaRoundTrip:
    def test_basic_write_parse(self, tmp_path):
        entry = MemoryEntry(
            name="my-memory",
            description="A test memory",
            body="This is the body.",
            metadata={"type": "user"},
        )
        p = tmp_path / "my-memory.md"
        write_entry(p, entry)

        loaded = parse_entry(p)
        assert loaded.name == "my-memory"
        assert loaded.description == "A test memory"
        assert loaded.body == "This is the body."
        assert loaded.type == "user"
        assert loaded.status == "living"

    def test_extended_fields_round_trip(self, tmp_path):
        entry = MemoryEntry(
            name="old-fact",
            description="Superseded fact",
            body="Body.",
            status="superseded",
            superseded_by="new-fact",
            keywords=["git", "ci"],
            confidence=0.9,
            provenance={"session": "abc123"},
        )
        p = tmp_path / "old-fact.md"
        write_entry(p, entry)

        loaded = parse_entry(p)
        assert loaded.status == "superseded"
        assert loaded.superseded_by == "new-fact"
        assert "git" in loaded.keywords
        assert loaded.confidence == pytest.approx(0.9)
        assert loaded.provenance["session"] == "abc123"

    def test_cc_format_compatible(self, tmp_path):
        """Files written by CC (no extended fields) parse correctly."""
        cc_content = (
            '---\nname: prefer-short\ndescription: "User prefers short answers"\n'
            "metadata:\n  type: user\n---\n\nBody text here.\n"
        )
        p = tmp_path / "prefer-short.md"
        p.write_text(cc_content, encoding="utf-8")

        entry = parse_entry(p)
        assert entry.name == "prefer-short"
        assert entry.description == "User prefers short answers"
        assert entry.type == "user"
        assert entry.status == "living"
        assert entry.body == "Body text here."

    def test_no_frontmatter(self, tmp_path):
        """Files without frontmatter parse gracefully."""
        p = tmp_path / "bare-file.md"
        p.write_text("Just a plain markdown file.\n", encoding="utf-8")
        entry = parse_entry(p)
        assert entry.name == "bare-file"
        assert entry.body == "Just a plain markdown file."

    def test_living_default_not_written(self, tmp_path):
        """Default status=living is NOT written to file (CC compatibility)."""
        entry = MemoryEntry(name="x", description="d", body="b")
        p = tmp_path / "x.md"
        write_entry(p, entry)
        raw = p.read_text(encoding="utf-8")
        assert "status:" not in raw

    def test_is_living(self, tmp_path):
        entry = MemoryEntry(name="x", description="d", body="b")
        assert entry.is_living()
        entry.status = "superseded"
        assert not entry.is_living()


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------


class TestRootResolution:
    def test_resolve_roots_returns_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
        roots = resolve_roots(workspace=tmp_path)
        assert isinstance(roots, list)
        assert len(roots) >= 2  # project + cc + user

    def test_env_override_prepended(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom-mem"
        monkeypatch.setenv("GPTME_MEMORY_DIRS", str(custom))
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        roots = resolve_roots(workspace=tmp_path)
        assert roots[0] == custom

    def test_write_root_cc(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        root = write_root(scope="cc", workspace=tmp_path)
        # Must be inside ~/.claude/projects/
        assert ".claude" in str(root)

    def test_write_root_project(self, tmp_path):
        root = write_root(scope="project", workspace=tmp_path)
        assert root == tmp_path / "memory"

    def test_write_root_user(self, tmp_path, monkeypatch):
        root = write_root(scope="user", workspace=tmp_path)
        assert root.parts[-1] == "memory"
        assert "gptme" in str(root)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class TestSaveMemory:
    def test_save_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        path = save_memory(
            "test-mem",
            "First line description.\n\nBody here.",
            workspace=tmp_path,
        )
        assert Path(path).exists()

    def test_save_slugifies_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        path = save_memory("My Cool Memory!", "desc.\n\nbody.", workspace=tmp_path)
        assert Path(path).name == "my-cool-memory.md"

    def test_save_updates_index(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        save_memory("mem-a", "Description A.\n\nbody.", workspace=tmp_path)
        # MEMORY.md written in the cc dir — find it
        from gptme.dirs import get_cc_memory_dir

        index = get_cc_memory_dir(tmp_path) / "MEMORY.md"
        assert index.exists()
        text = index.read_text(encoding="utf-8")
        assert "mem-a" in text

    def test_save_with_scope_project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        path = save_memory(
            "proj-mem",
            "desc.\n\nbody.",
            scope="project",
            workspace=tmp_path,
        )
        assert Path(path).parent == tmp_path / "memory"

    def test_save_idempotent_slug(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        p1 = save_memory("same name", "desc.\n\nbody.", workspace=tmp_path)
        p2 = save_memory("same-name", "desc2.\n\nbody2.", workspace=tmp_path)
        # Same slug → same file
        assert p1 == p2


class TestListEntries:
    def test_list_empty_roots(self, tmp_path):
        entries = list_entries(roots=[tmp_path])
        assert entries == []

    def test_list_finds_entries(self, tmp_path):
        for name in ("alpha", "beta", "gamma"):
            entry = MemoryEntry(name=name, description=f"Desc {name}", body="body")
            write_entry(tmp_path / f"{name}.md", entry)

        entries = list_entries(roots=[tmp_path])
        assert len(entries) == 3
        names = {e.name for e in entries}
        assert names == {"alpha", "beta", "gamma"}

    def test_list_skips_superseded_by_default(self, tmp_path):
        alive = MemoryEntry(name="alive", description="d", body="b")
        dead = MemoryEntry(name="dead", description="d", body="b", status="superseded")
        write_entry(tmp_path / "alive.md", alive)
        write_entry(tmp_path / "dead.md", dead)

        entries = list_entries(roots=[tmp_path])
        assert len(entries) == 1
        assert entries[0].name == "alive"

    def test_list_includes_superseded_when_flag_set(self, tmp_path):
        alive = MemoryEntry(name="alive", description="d", body="b")
        dead = MemoryEntry(name="dead", description="d", body="b", status="superseded")
        write_entry(tmp_path / "alive.md", alive)
        write_entry(tmp_path / "dead.md", dead)

        entries = list_entries(roots=[tmp_path], include_superseded=True)
        assert len(entries) == 2

    def test_list_deduplicates_across_roots(self, tmp_path):
        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        root1.mkdir()
        root2.mkdir()
        entry = MemoryEntry(name="shared", description="d", body="b")
        write_entry(root1 / "shared.md", entry)
        write_entry(root2 / "shared.md", entry)

        entries = list_entries(roots=[root1, root2])
        assert len(entries) == 1
        assert entries[0].path and entries[0].path.parent == root1


class TestShowEntry:
    def test_show_found(self, tmp_path):
        entry = MemoryEntry(name="my-entry", description="d", body="b")
        write_entry(tmp_path / "my-entry.md", entry)

        found = show_entry("my-entry", roots=[tmp_path])
        assert found is not None
        assert found.name == "my-entry"

    def test_show_not_found(self, tmp_path):
        assert show_entry("nonexistent", roots=[tmp_path]) is None

    def test_show_slugifies_query(self, tmp_path):
        entry = MemoryEntry(name="my-entry", description="d", body="b")
        write_entry(tmp_path / "my-entry.md", entry)

        # Query with spaces/caps should still find the slugified name
        found = show_entry("My Entry", roots=[tmp_path])
        assert found is not None


class TestIndexMemory:
    def test_index_generates_content(self, tmp_path):
        for name in ("alpha", "beta"):
            entry = MemoryEntry(name=name, description=f"Desc {name}", body="body")
            write_entry(tmp_path / f"{name}.md", entry)

        content = index_memory(roots=[tmp_path])
        assert "# Memory" in content
        assert "alpha" in content
        assert "beta" in content

    def test_index_budget(self, tmp_path):
        for i in range(10):
            entry = MemoryEntry(name=f"entry-{i}", description=f"d{i}", body="b")
            write_entry(tmp_path / f"entry-{i}.md", entry)

        content = index_memory(roots=[tmp_path], budget=3)
        # Only 3 entries should appear
        assert content.count("- [") == 3

    def test_index_check_stable(self, tmp_path):
        entry = MemoryEntry(name="x", description="d", body="b")
        write_entry(tmp_path / "x.md", entry)

        # Generate and write the index
        content = index_memory(roots=[tmp_path])
        (tmp_path / "MEMORY.md").write_text(content, encoding="utf-8")

        # Second call with check=True should return ""
        result = index_memory(roots=[tmp_path], check=True)
        assert result == ""

    def test_index_check_not_stable(self, tmp_path):
        entry = MemoryEntry(name="x", description="d", body="b")
        write_entry(tmp_path / "x.md", entry)
        (tmp_path / "MEMORY.md").write_text("old content\n", encoding="utf-8")

        result = index_memory(roots=[tmp_path], check=True)
        assert result != ""


class TestSupersede:
    def test_supersede_marks_both(self, tmp_path):
        old = MemoryEntry(name="old-mem", description="old", body="old body")
        new = MemoryEntry(name="new-mem", description="new", body="new body")
        write_entry(tmp_path / "old-mem.md", old)
        write_entry(tmp_path / "new-mem.md", new)

        result = supersede("old-mem", "new-mem", roots=[tmp_path])
        assert result is not None
        updated_old, updated_new = result
        assert updated_old.status == "superseded"
        assert updated_old.superseded_by == "new-mem"
        assert updated_new.supersedes == "old-mem"

        # Verify persisted to disk
        reloaded_old = parse_entry(tmp_path / "old-mem.md")
        assert reloaded_old.status == "superseded"

    def test_supersede_not_found(self, tmp_path):
        result = supersede("missing", "also-missing", roots=[tmp_path])
        assert result is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestMemoryCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def cli(self):
        from gptme.cli.cmd_memory import memory

        return memory

    def test_save_and_list(self, runner, cli, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        result = runner.invoke(
            cli,
            ["save", "test-mem", "First line.\n\nBody text.", "--scope", "project"],
        )
        assert result.exit_code == 0, result.output

        result = runner.invoke(cli, ["list", "--scope", "project"])
        assert result.exit_code == 0, result.output
        assert "test-mem" in result.output

    def test_save_and_show(self, runner, cli, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        runner.invoke(
            cli,
            [
                "save",
                "my-fact",
                "Fact description.\n\nFull body.",
                "--scope",
                "project",
            ],
        )
        result = runner.invoke(cli, ["show", "my-fact"])
        assert result.exit_code == 0, result.output
        assert "my-fact" in result.output

    def test_save_json_output(self, runner, cli, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        result = runner.invoke(
            cli,
            ["save", "json-test", "Desc.\n\nBody.", "--scope", "project", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "json-test"

    def test_index_command(self, runner, cli, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
        runner.invoke(
            cli,
            ["save", "entry-a", "A desc.\n\nbody.", "--scope", "project"],
        )
        result = runner.invoke(cli, ["index"])
        assert result.exit_code == 0, result.output
        assert "# Memory" in result.output

    def test_audit_command(self, runner, cli, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        result = runner.invoke(cli, ["audit"])
        assert result.exit_code == 0, result.output
        assert "audit" in result.output.lower()

    def test_show_not_found(self, runner, cli, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        result = runner.invoke(cli, ["show", "nonexistent"])
        assert result.exit_code != 0

    def test_save_empty_content_error(self, runner, cli, tmp_path, monkeypatch):
        monkeypatch.setenv("GPTME_WORKSPACE", str(tmp_path))
        result = runner.invoke(cli, ["save", "empty", "   "])
        assert result.exit_code != 0
