"""Tests for gptme.memory: schema round-trip, layered roots, store, index, CLI."""

import importlib
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.util import main as util_main
from gptme.memory import (
    MemoryEntry,
    MemoryParseError,
    MemoryRoot,
    MemoryStore,
    RecallBackendUnavailable,
    parse_entry,
    recall,
    render_recall,
    resolve_roots,
    slugify,
)
from gptme.memory.recall import RecallHit
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

    def test_supersede_links_both_entries_and_regenerates_index(self, tmp_path):
        store = self._store(tmp_path)
        store.save("old-belief", "Old belief", "Old body", scope="project")
        store.save("new-belief", "New belief", "New body", scope="project")

        old, new = store.supersede("old-belief", "new-belief", scope="project")

        assert old.status == "superseded"
        assert old.superseded_by == "new-belief"
        assert new.supersedes == ["old-belief"]
        assert not old.is_living
        reparsed_old = parse_entry(tmp_path / "project" / "old-belief.md")
        reparsed_new = parse_entry(tmp_path / "project" / "new-belief.md")
        assert reparsed_old.superseded_by == "new-belief"
        assert reparsed_new.supersedes == ["old-belief"]
        index = (tmp_path / "project" / "MEMORY.md").read_text()
        assert "new-belief.md" in index
        assert "old-belief.md" not in index

    def test_supersede_requires_strict_frontmatter_before_rewriting(self, tmp_path):
        root = tmp_path / "project"
        _write(
            root,
            "old",
            "---\nname: old\ndescription: unquoted: colon\n---\nold body\n",
        )
        _write(root, "new", "---\nname: new\ndescription: fine\n---\nnew body\n")
        store = self._store(tmp_path)

        with pytest.raises(ValueError, match="invalid YAML"):
            store.supersede("old", "new", scope="project")

        assert "status: superseded" not in (root / "old.md").read_text()

    def test_supersede_rolls_back_entries_when_index_write_fails(
        self, tmp_path, monkeypatch
    ):
        store = self._store(tmp_path)
        store.save("old", "old", scope="project")
        store.save("new", "new", scope="project")
        root = tmp_path / "project"
        before = {path.name: path.read_text() for path in root.glob("*.md")}

        def fail_index(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store, "write_index", fail_index)
        with pytest.raises(OSError, match="disk full"):
            store.supersede("old", "new", scope="project")

        assert {path.name: path.read_text() for path in root.glob("*.md")} == before

    def test_supersede_rejects_cross_root_entries(self, tmp_path):
        store = self._store(tmp_path)
        store.save("old", "old", scope="project")
        store.save("new", "new", scope="user")

        with pytest.raises(KeyError, match="new"):
            store.supersede("old", "new", scope="project")

    def test_supersede_rejects_self_and_non_living_replacement(self, tmp_path):
        store = self._store(tmp_path)
        store.save("one", "one", scope="project")
        store.save("two", "two", scope="project")

        with pytest.raises(ValueError, match="itself"):
            store.supersede("one", "one", scope="project")
        store.supersede("one", "two", scope="project")
        # Repeating the exact supersession is idempotent.
        store.supersede("one", "two", scope="project")
        store.save("three", "three", scope="project")
        with pytest.raises(ValueError, match="not living"):
            store.supersede("three", "one", scope="project")
        with pytest.raises(ValueError, match="not living"):
            store.supersede("one", "three", scope="project")

    @pytest.mark.parametrize(
        "field",
        [
            "status: bogus",
            "confidence: nope",
            "confidence: 2",
            "metadata: nope",
            "provenance: nope",
            "supersedes: 42",
            "keywords: [fine, 42]",
        ],
    )
    def test_audit_reports_invalid_field_types(self, tmp_path, field):
        root = tmp_path / "project"
        _write(
            root,
            "invalid",
            f"---\nname: invalid\ndescription: invalid\n{field}\n---\nbody\n",
        )

        issues = self._store(tmp_path).audit(scope="project")

        assert len(issues) == 1
        assert issues[0].code == "invalid-yaml"

    def test_audit_reports_duplicate_names(self, tmp_path):
        root = tmp_path / "project"
        _write(root, "a", "---\nname: same\ndescription: first\n---\n")
        _write(root, "z", "---\nname: same\ndescription: second\n---\n")

        issues = self._store(tmp_path).audit(scope="project")

        assert [(issue.code, issue.entry) for issue in issues] == [
            ("duplicate-name", "z.md")
        ]

    def test_audit_reports_invalid_yaml_and_broken_supersession(self, tmp_path):
        root = tmp_path / "project"
        _write(
            root,
            "invalid",
            "---\nname: invalid\ndescription: unquoted: colon\n---\nbody\n",
        )
        _write(
            root,
            "old",
            "---\nname: old\ndescription: old\nstatus: superseded\nsuperseded_by: missing\n---\n",
        )
        store = self._store(tmp_path)

        issues = store.audit(scope="project")

        assert {(issue.code, issue.entry) for issue in issues} >= {
            ("invalid-yaml", "invalid.md"),
            ("dangling-superseded-by", "old"),
        }

    def test_audit_reports_asymmetric_reverse_link(self, tmp_path):
        root = tmp_path / "project"
        _write(
            root,
            "old",
            "---\nname: old\ndescription: old\nstatus: superseded\nsuperseded_by: new\n---\n",
        )
        _write(root, "new", "---\nname: new\ndescription: new\n---\n")

        issues = self._store(tmp_path).audit(scope="project")

        assert {(issue.code, issue.entry) for issue in issues} == {
            ("asymmetric-supersession", "old")
        }

    def test_audit_reports_dangling_and_asymmetric_forward_links(self, tmp_path):
        root = tmp_path / "project"
        _write(
            root,
            "new",
            "---\nname: new\ndescription: new\nsupersedes: [missing, old]\n---\n",
        )
        _write(root, "old", "---\nname: old\ndescription: old\n---\n")

        issues = self._store(tmp_path).audit(scope="project")

        assert {(issue.code, issue.entry) for issue in issues} == {
            ("dangling-supersedes", "new"),
            ("asymmetric-supersession", "new"),
        }

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


class TestRecall:
    @staticmethod
    def _entry(
        root: Path,
        name: str,
        description: str,
        body: str = "",
        *,
        status: str = "living",
    ) -> MemoryEntry:
        path = _write(
            root,
            name,
            "---\n"
            f"name: {name}\n"
            f'description: "{description}"\n'
            f"status: {status}\n"
            "---\n"
            f"{body}\n",
        )
        return parse_entry(path, scope="explicit")

    def test_overlap_fallback_ranks_all_layered_roots(self, tmp_path, monkeypatch):
        near = tmp_path / "near"
        far = tmp_path / "far"
        self._entry(
            near,
            "openrouter-guardrail",
            "OpenRouter guardrail blocks Anthropic model IDs",
            "Use another provider when Anthropic requests return 404.",
        )
        self._entry(
            far,
            "slot-refresh",
            "Refresh dead Claude subscription slots",
            "An invalid_grant means the slot needs login.",
        )
        self._entry(
            far,
            "old-guardrail",
            "OpenRouter guardrail historical note",
            status="superseded",
        )
        store = MemoryStore([MemoryRoot("explicit", near), MemoryRoot("user", far)])

        def unavailable(*_args, **_kwargs):
            raise RecallBackendUnavailable("gptme-rag unavailable")

        recall_module = importlib.import_module("gptme.memory.recall")
        monkeypatch.setattr(recall_module, "_recall_tfidf", unavailable)
        result = recall(
            store,
            "Why are Anthropic model requests blocked by the OpenRouter guardrail?",
            limit=3,
        )
        assert result.backend == "overlap"
        assert [hit.entry.name for hit in result.hits] == ["openrouter-guardrail"]
        assert result.hits[0].matched_terms == [
            "anthropic",
            "guardrail",
            "model",
            "openrouter",
            "requests",
        ]

        second = recall(
            store,
            "The Claude slot says invalid grant and needs login refresh",
            backend="overlap",
        )
        assert [hit.entry.name for hit in second.hits] == ["slot-refresh"]
        assert second.hits[0].entry.scope == "user"

    def test_auto_prefers_tfidf_when_available(self, tmp_path, monkeypatch):
        entry = self._entry(tmp_path, "one", "One useful memory")
        store = MemoryStore([MemoryRoot("explicit", tmp_path)])
        expected = [RecallHit(entry=entry, score=0.42, matched_terms=[])]
        recall_module = importlib.import_module("gptme.memory.recall")
        monkeypatch.setattr(
            recall_module,
            "_recall_tfidf",
            lambda entries, query, limit: expected,
        )
        result = recall(store, "useful memory", backend="auto")
        assert result.backend == "tfidf"
        assert result.hits == expected

    def test_real_tfidf_backend_when_optional_package_is_installed(self, tmp_path):
        pytest.importorskip("gptme_rag.lexical")
        store = MemoryStore([MemoryRoot("explicit", tmp_path)])
        self._entry(
            tmp_path,
            "guardrail",
            "OpenRouter guardrail blocks Anthropic models",
            "The account returns a provider-specific 404.",
        )
        self._entry(
            tmp_path,
            "database",
            "PostgreSQL migration procedure",
            "Back up the database before applying migrations.",
        )
        result = recall(
            store,
            "Why does the OpenRouter Anthropic guardrail return 404?",
            backend="tfidf",
        )
        assert result.backend == "tfidf"
        assert [hit.entry.name for hit in result.hits] == ["guardrail"]
        assert result.hits[0].score > 0

    def test_forced_tfidf_does_not_silently_fallback(self, tmp_path, monkeypatch):
        store = MemoryStore([MemoryRoot("explicit", tmp_path)])
        self._entry(tmp_path, "one", "One useful memory")

        def unavailable(*_args, **_kwargs):
            raise RecallBackendUnavailable("install gptme-rag[lexical]")

        recall_module = importlib.import_module("gptme.memory.recall")
        monkeypatch.setattr(recall_module, "_recall_tfidf", unavailable)
        with pytest.raises(RecallBackendUnavailable, match="gptme-rag"):
            recall(store, "useful memory", backend="tfidf")

    def test_render_bounds_and_flattens_entry_body(self, tmp_path):
        store = MemoryStore([MemoryRoot("explicit", tmp_path)])
        self._entry(tmp_path, "one", "Useful memory", "line one\n" + "x" * 80)
        result = recall(store, "one useful memory", backend="overlap")
        rendered = render_recall(result, body_chars=20)
        assert "line one xxxxxxxx..." in rendered
        assert "line one\n" not in rendered

    def test_render_tiny_body_chars_does_not_bypass_bound(self, tmp_path):
        store = MemoryStore([MemoryRoot("explicit", tmp_path)])
        self._entry(tmp_path, "one", "Useful memory", "line one\n" + "x" * 80)
        result = recall(store, "one useful memory", backend="overlap")
        for body_chars in (1, 2, 3):
            tiny = render_recall(result, body_chars=body_chars)
            body_line = next(
                line
                for line in tiny.splitlines()
                if line.startswith("  ") and not line.startswith("  Match:")
            )
            assert len(body_line[2:]) <= body_chars
            assert "x" * 10 not in tiny


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

    def test_supersede_and_audit_cli(self, env):
        runner = CliRunner()
        for name in ("old", "new"):
            result = runner.invoke(
                util_main,
                ["memory", "save", name, f"{name} description"],
            )
            assert result.exit_code == 0, result.output

        result = runner.invoke(util_main, ["memory", "supersede", "old", "new"])
        assert result.exit_code == 0, result.output
        assert "old -> new" in result.output
        result = runner.invoke(util_main, ["memory", "audit", "--quiet"])
        assert result.exit_code == 0, result.output
        assert result.output == ""

    def test_audit_cli_fails_on_invalid_yaml(self, env):
        _write(
            env,
            "invalid",
            "---\nname: invalid\ndescription: unquoted: colon\n---\nbody\n",
        )

        result = CliRunner().invoke(util_main, ["memory", "audit"])

        assert result.exit_code == 1
        assert "invalid-yaml" in result.output
        assert "invalid.md" in result.output

    def test_recall_hook_json_reads_claude_payload(self, env):
        _write(
            env,
            "provider-policy",
            "---\n"
            "name: provider-policy\n"
            'description: "Keep private code off training providers"\n'
            "metadata:\n"
            "  type: feedback\n"
            "---\n"
            "Route sensitive review through a private provider.\n",
        )
        result = CliRunner().invoke(
            util_main,
            [
                "memory",
                "recall",
                "--prompt",
                "-",
                "--backend",
                "overlap",
                "--format",
                "hook-json",
            ],
            input='{"prompt":"Which provider should review private sensitive code?"}',
        )
        assert result.exit_code == 0, result.output
        payload = __import__("json").loads(result.output)
        hook = payload["hookSpecificOutput"]
        assert hook["hookEventName"] == "UserPromptSubmit"
        assert "provider-policy" in hook["additionalContext"]
        assert "backend=overlap" in hook["additionalContext"]

    def test_recall_json_discloses_backend_and_empty_results(self, env):
        result = CliRunner().invoke(
            util_main,
            [
                "memory",
                "recall",
                "no matching signal here",
                "--backend",
                "overlap",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = __import__("json").loads(result.output)
        assert payload == {"backend": "overlap", "hits": []}

    def test_roots(self, env):
        r = CliRunner().invoke(util_main, ["memory", "roots"])
        assert r.exit_code == 0 and "explicit" in r.output and "(missing)" in r.output


class TestCodexAgentsMdPattern:
    """Exercises the Codex / AGENTS.md integration pattern.

    Codex has no hook mechanism; it drives memory via shell commands listed in
    AGENTS.md. These tests verify that the exact CLI patterns documented there
    and in docs/memory.rst produce the expected round-trip behaviour.
    """

    @pytest.fixture
    def mem_env(self, tmp_path, monkeypatch):
        """Isolated memory root so tests don't interact with real user memory."""
        root = tmp_path / "mem"
        monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
        return root

    def test_save_from_agents_md_pattern(self, mem_env):
        """gptme-util memory save <slug> "<desc>" --type feedback <<'EOF' ... EOF"""
        runner = CliRunner()
        r = runner.invoke(
            util_main,
            [
                "memory",
                "save",
                "codex-pref",
                "Prefer succinct responses.",
                "--type",
                "feedback",
            ],
            input="Keep answers short and code-first.\n",
        )
        assert r.exit_code == 0, r.output
        # File written to the explicit root
        assert (mem_env / "codex-pref.md").exists()
        content = (mem_env / "codex-pref.md").read_text()
        assert "name: codex-pref" in content
        assert "Prefer succinct responses." in content
        assert "Keep answers short and code-first." in content

    def test_recall_plain_text_from_agents_md_pattern(self, mem_env):
        """gptme-util memory recall "<query>" -k 5  (AGENTS.md session-start pattern)."""
        _write(
            mem_env,
            "provider-policy",
            "---\n"
            "name: provider-policy\n"
            'description: "Route sensitive code through private providers"\n'
            "metadata:\n"
            "  type: feedback\n"
            "---\n"
            "Never send private code to public training providers.\n",
        )
        runner = CliRunner()
        r = runner.invoke(
            util_main,
            [
                "memory",
                "recall",
                "which provider for sensitive code review",
                "-k",
                "5",
            ],
        )
        assert r.exit_code == 0, r.output
        # Documented no-flag command names the backend that actually ran.
        # Mirror recall()'s auto path: try the optional TF-IDF import, else overlap.
        try:
            import gptme_rag.lexical  # noqa: F401
        except ImportError:
            expected_backend = "overlap"
        else:
            expected_backend = "tfidf"
        assert f"backend={expected_backend}" in r.output
        # Plain text output must contain the matched entry name and body text
        assert "provider-policy" in r.output
        assert "private code" in r.output

    def test_cross_harness_roundtrip(self, tmp_path, monkeypatch):
        """Codex-written memory is reachable from a reader whose write root differs.

        Save lands on the Codex write root. A second harness then lists/recalls
        with its own root first and the Codex root still in the layer list —
        the lookup that would miss if list/recall only searched the write root.
        """
        write_root = tmp_path / "codex"
        reader_root = tmp_path / "cc"
        write_root.mkdir()
        reader_root.mkdir()
        monkeypatch.setenv(
            "GPTME_MEMORY_DIRS", os.pathsep.join((str(write_root), str(reader_root)))
        )
        runner = CliRunner()
        r = runner.invoke(
            util_main,
            [
                "memory",
                "save",
                "xharness",
                "Cross-harness test entry.",
                "--type",
                "project",
            ],
            input="Written by a Codex session to test cross-harness sharing.\n",
        )
        assert r.exit_code == 0, r.output
        assert (write_root / "xharness.md").exists()
        assert not (reader_root / "xharness.md").exists()

        # Reader harness: own root first, Codex write root still layered in
        monkeypatch.setenv(
            "GPTME_MEMORY_DIRS", os.pathsep.join((str(reader_root), str(write_root)))
        )
        r = runner.invoke(util_main, ["memory", "list"])
        assert r.exit_code == 0 and "xharness" in r.output

        r = runner.invoke(
            util_main,
            ["memory", "recall", "Cross-harness test entry", "-k", "5"],
        )
        assert r.exit_code == 0, r.output
        assert "xharness" in r.output
        assert "Written by a Codex session to test cross-harness sharing." in r.output

    def test_save_all_valid_types(self, mem_env):
        """All four types documented in AGENTS.md are accepted without error."""
        runner = CliRunner()
        for type_ in ("user", "feedback", "project", "reference"):
            r = runner.invoke(
                util_main,
                [
                    "memory",
                    "save",
                    f"test-{type_}",
                    f"Test {type_} entry.",
                    "--type",
                    type_,
                ],
                input=f"Body for {type_}.\n",
            )
            assert r.exit_code == 0, f"--type {type_} failed: {r.output}"
            assert (mem_env / f"test-{type_}.md").exists()


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
