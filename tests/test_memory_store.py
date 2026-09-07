"""Tests for gptme.memory: schema round-trip, layered roots, store, index, CLI."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.util import main as util_main
from gptme.memory import (
    MemoryEntry,
    MemoryParseError,
    MemoryRoot,
    MemoryStore,
    parse_entry,
    resolve_roots,
    slugify,
)
from gptme.memory.roots import default_write_root
from gptme.memory.schema import entry_from_text

CC_FILE = """---
name: prefer-short-answers
description: "User prefers short, direct answers."
metadata:
  type: feedback
  originSessionId: abc-123
---

Erik said so on 2026-09-07.
"""


def _write(dir_: Path, name: str, text: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return p


class TestSchema:
    def test_parse_cc_file(self, tmp_path):
        e = parse_entry(_write(tmp_path, "prefer-short-answers", CC_FILE))
        assert e.name == "prefer-short-answers"
        assert e.type == "feedback"
        assert e.description == "User prefers short, direct answers."
        assert e.metadata == {"originSessionId": "abc-123"}
        assert e.body == "Erik said so on 2026-09-07."
        assert e.is_living

    def test_render_round_trip(self):
        e = entry_from_text(CC_FILE)
        again = entry_from_text(e.to_markdown())
        assert again.to_dict() == e.to_dict()
        assert again.body == e.body
        # CC fields keep their exact shape
        assert (
            "metadata:\n  type: feedback\n  originSessionId: abc-123" in e.to_markdown()
        )

    def test_optional_fields_round_trip(self):
        e = MemoryEntry(
            name="x",
            description="d",
            type="project",
            title="X the thing",
            status="superseded",
            superseded_by="y",
            supersedes=["w"],
            provenance={"session": "b831", "source": "knowledge/design/a.md"},
            confidence=0.9,
            keywords=["pacing floor", "fable window"],
            recheck="2026-09-28",
            body="body",
        )
        again = entry_from_text(e.to_markdown())
        assert again.to_dict() == e.to_dict()
        assert again.index_line() == "- [X the thing](x.md) — d"

    def test_top_level_type_and_lenient_yaml(self, tmp_path):
        # Unquoted colon makes PyYAML fail; the lenient parser still yields an entry.
        text = "---\nname: odd\ndescription: note: this has a colon\ntype: reference\n---\nbody\n"
        e = parse_entry(_write(tmp_path, "odd", text))
        assert e.type == "reference"
        assert "colon" in e.description

    def test_no_frontmatter_raises(self, tmp_path):
        with pytest.raises(MemoryParseError):
            parse_entry(_write(tmp_path, "plain", "# Just a heading\n"))

    def test_name_falls_back_to_stem(self, tmp_path):
        e = parse_entry(_write(tmp_path, "from-stem", "---\ndescription: d\n---\n"))
        assert e.name == "from-stem"

    def test_slugify(self):
        assert slugify("My Cool Fact!") == "my-cool-fact"
        assert slugify("") == "memory"


class TestRoots:
    def test_explicit_env_overrides_everything(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        roots = resolve_roots(tmp_path, env={"GPTME_MEMORY_DIRS": f"{a}:{b}"})
        assert [r.scope for r in roots] == ["explicit", "explicit"]
        assert [r.path for r in roots] == [a, b]

    def test_layer_order(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        (ws / "memory").mkdir(parents=True)
        agent = tmp_path / "brain"
        (agent / "memory").mkdir(parents=True)
        cfg = tmp_path / "cfg"
        monkeypatch.setattr(
            "gptme.memory.roots.get_cc_memory_dir", lambda _ws: tmp_path / "cc"
        )
        roots = resolve_roots(
            ws, env={"GPTME_AGENT_WORKSPACE": str(agent)}, config_dir=cfg
        )
        assert [r.scope for r in roots] == ["project", "cc", "agent", "user"]
        assert roots[0].path == ws / "memory"
        assert roots[3].path == cfg / "memory"
        assert default_write_root(roots).scope == "project"

    def test_same_dir_collapses_onto_first_scope(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        (ws / "memory").mkdir(parents=True)
        monkeypatch.setattr(
            "gptme.memory.roots.get_cc_memory_dir", lambda _ws: ws / "memory"
        )
        roots = resolve_roots(ws, env={}, config_dir=tmp_path / "cfg")
        assert [r.scope for r in roots] == ["project", "user"]

    def test_cc_is_default_write_root_without_project_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "gptme.memory.roots.get_cc_memory_dir", lambda _ws: tmp_path / "cc"
        )
        roots = resolve_roots(tmp_path / "ws", env={}, config_dir=tmp_path / "cfg")
        assert default_write_root(roots).scope == "cc"


class TestStore:
    def _store(self, tmp_path) -> MemoryStore:
        return MemoryStore(
            [
                MemoryRoot("project", tmp_path / "project"),
                MemoryRoot("user", tmp_path / "user"),
            ]
        )

    def test_union_and_nearest_wins(self, tmp_path):
        _write(
            tmp_path / "project",
            "shared",
            "---\nname: shared\ndescription: near\n---\n",
        )
        _write(
            tmp_path / "user", "shared", "---\nname: shared\ndescription: far\n---\n"
        )
        _write(
            tmp_path / "user",
            "only-user",
            "---\nname: only-user\ndescription: u\n---\n",
        )
        entries = self._store(tmp_path).entries()
        assert [e.name for e in entries] == ["only-user", "shared"]
        assert entries[1].description == "near"
        assert entries[1].scope == "project"

    def test_skips_index_and_non_entries(self, tmp_path):
        _write(tmp_path / "project", "MEMORY", "# Persistent Memory\n")
        _write(tmp_path / "project", "MEMORY-archive", "- old\n")
        _write(tmp_path / "project", "notes", "no frontmatter\n")
        _write(tmp_path / "project", "real", "---\nname: real\ndescription: r\n---\n")
        store = self._store(tmp_path)
        assert [e.name for e in store.entries()] == ["real"]
        assert [p.name for p, _ in store.errors] == ["notes.md"]

    def test_save_writes_file_and_index_line(self, tmp_path):
        store = self._store(tmp_path)
        path = store.save("My Fact", "A fact.", "Detail.", type="project")
        assert path == tmp_path / "project" / "my-fact.md"
        text = path.read_text()
        assert 'description: "A fact."' in text and "type: project" in text
        index = (tmp_path / "project" / "MEMORY.md").read_text()
        assert index.startswith("# Persistent Memory\n\n")
        assert "- [my-fact](my-fact.md) — A fact." in index
        # re-save updates the same line, never duplicates it
        store.save("my fact", "Changed.", type="project")
        index = (tmp_path / "project" / "MEMORY.md").read_text()
        assert index.count("my-fact.md") == 1 and "Changed." in index

    def test_save_to_scope_and_unknown_scope(self, tmp_path):
        store = self._store(tmp_path)
        assert store.save("u", "d", scope="user").parent == tmp_path / "user"
        with pytest.raises(KeyError):
            store.save("u", "d", scope="agent")

    def test_index_groups_by_type_and_is_byte_stable(self, tmp_path):
        store = self._store(tmp_path)
        store.save("b-proj", "B", type="project")
        store.save("a-proj", "A", type="project")
        store.save("fb", "F", type="feedback")
        store.save("custom", "C", type="decision")
        _write(
            tmp_path / "project",
            "old",
            "---\nname: old\ndescription: gone\nstatus: historical\n---\n",
        )
        assert not store.check_index()
        store.write_index()
        text = (tmp_path / "project" / "MEMORY.md").read_text()
        assert text == (
            "# Persistent Memory\n\n"
            "## Feedback\n- [fb](fb.md) — F\n\n"
            "## Project\n- [a-proj](a-proj.md) — A\n- [b-proj](b-proj.md) — B\n\n"
            "## Decision\n- [custom](custom.md) — C\n"
        )
        assert "gone" not in text
        assert store.check_index()

    def test_index_budget_omits_deterministically(self, tmp_path):
        store = self._store(tmp_path)
        for i in range(20):
            store.save(f"e{i:02d}", "x" * 40, type="project")
        full = store.render_index(store.entries())
        small = store.render_index(store.entries(), budget=400)
        assert len(small.encode()) <= 400
        assert "more entries omitted" in small
        assert small == store.render_index(store.entries(), budget=400)
        assert len(full.encode()) > 400

    def test_index_budget_counts_entries_in_later_groups(self, tmp_path):
        store = self._store(tmp_path)
        store.save("first", "x" * 80, type="feedback")
        store.save("second", "x" * 80, type="project")
        text = store.render_index(store.entries(), budget=130)
        assert len(text.encode()) <= 130
        assert "2 more entries omitted" in text

    def test_index_budget_too_small_raises(self, tmp_path):
        store = self._store(tmp_path)
        store.save("first", "x" * 80, type="feedback")
        with pytest.raises(ValueError, match="too small"):
            store.render_index(store.entries(), budget=1)
        with pytest.raises(ValueError, match="too small"):
            store.render_index(store.entries(), budget=0)

    def test_index_uses_only_selected_physical_root(self, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        _write(first, "one", "---\nname: one\ndescription: first\n---\n")
        _write(second, "two", "---\nname: two\ndescription: second\n---\n")
        store = MemoryStore(
            [MemoryRoot("explicit", first), MemoryRoot("explicit", second)]
        )
        store.write_index("explicit")
        text = (first / "MEMORY.md").read_text()
        assert "one.md" in text
        assert "two.md" not in text


class TestCli:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        root = tmp_path / "mem"
        monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
        return root

    def test_save_list_show_index(self, env):
        runner = CliRunner()
        r = runner.invoke(
            util_main,
            ["memory", "save", "pref", "Short answers.", "--type", "feedback"],
            input="Body text.\n",
        )
        assert r.exit_code == 0, r.output
        assert (env / "pref.md").exists()

        r = runner.invoke(util_main, ["memory", "list", "--type", "feedback"])
        assert r.exit_code == 0 and "pref" in r.output

        r = runner.invoke(util_main, ["memory", "show", "pref", "--json"])
        assert r.exit_code == 0 and '"body": "Body text."' in r.output

        r = runner.invoke(util_main, ["memory", "show", "nope"])
        assert r.exit_code == 1

        r = runner.invoke(util_main, ["memory", "index", "--check"])
        assert r.exit_code == 1 and "DRIFT" in r.output
        r = runner.invoke(util_main, ["memory", "index", "--write"])
        assert r.exit_code == 0
        r = runner.invoke(util_main, ["memory", "index", "--check"])
        assert r.exit_code == 0, r.output

    def test_human_output_strips_controls_but_preserves_lines(self, env):
        _write(
            env,
            "unsafe",
            '---\nname: unsafe\ndescription: "red \u001b[31mtext\u001b[0m"\n---\n\nline 1\r\nline 2\n',
        )
        runner = CliRunner()
        shown = runner.invoke(util_main, ["memory", "show", "unsafe"])
        assert shown.exit_code == 0
        assert "\x1b" not in shown.output
        assert "\r" not in shown.output
        assert "line 1\nline 2" in shown.output
        listed = runner.invoke(util_main, ["memory", "list"])
        assert "\x1b" not in listed.output
        assert "\r" not in listed.output
        indexed = runner.invoke(util_main, ["memory", "index"])
        assert "\x1b" not in indexed.output
        assert "\r" not in indexed.output

    def test_index_budget_cli_rejects_non_positive(self, env):
        r = CliRunner().invoke(util_main, ["memory", "index", "--budget", "0"])
        assert r.exit_code != 0

    def test_roots(self, env):
        r = CliRunner().invoke(util_main, ["memory", "roots"])
        assert r.exit_code == 0 and "explicit" in r.output and "(missing)" in r.output


@pytest.mark.skipif(
    not Path("/home/bob/bob/memory").is_dir(), reason="Bob's memory dir not present"
)
def test_bob_memory_dir_round_trip(tmp_path):
    """Real-world corpus: every CC-written entry parses and the index is byte-stable."""
    import shutil

    copy = tmp_path / "memory"
    shutil.copytree("/home/bob/bob/memory", copy)
    store = MemoryStore([MemoryRoot("explicit", copy)])
    entries = store.entries()
    assert len(entries) >= 400
    assert all(e.description for e in entries[:50])
    store.write_index()
    assert store.check_index()
