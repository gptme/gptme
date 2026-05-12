"""Tests for tool_target_instructions hook.

Covers the four scenarios from the design doc
(knowledge/technical-designs/tool-targeted-agent-instruction-loading.md):

- read file in subdir without cd
- patch file in worktree without cd
- identical-content dedup across repo/worktree
- no reinjection on repeated reads in same directory
"""

from pathlib import Path

import pytest

from gptme.hooks.agents_md_inject import _HASH_PREFIX, _get_loaded_files
from gptme.hooks.tool_target_instructions import (
    _MAX_BYTES_PER_EVENT,
    _MAX_INJECT_PER_EVENT,
    _candidate_directories,
    _extract_paths,
    on_tool_execute_post,
)
from gptme.logmanager import Log
from gptme.message import Message
from gptme.prompts import _loaded_agent_files_var
from gptme.tools.base import ToolUse
from gptme.util.context_dedup import _content_hash


@pytest.fixture
def empty_log() -> Log:
    return Log()


@pytest.fixture(autouse=True)
def reset_contextvars():
    token = _loaded_agent_files_var.set(None)
    yield
    _loaded_agent_files_var.reset(token)


def _make_use(tool: str, *, args=None, kwargs=None, content=None) -> ToolUse:
    return ToolUse(tool=tool, args=args, content=content, kwargs=kwargs)


# ---------------------------------------------------------------------------
# Argument extraction
# ---------------------------------------------------------------------------


class TestExtractPaths:
    def test_kwargs_path(self):
        use = _make_use("read", kwargs={"path": "/tmp/foo.md"})
        assert _extract_paths(use) == [Path("/tmp/foo.md")]

    def test_positional_args(self):
        use = _make_use("save", args=["/tmp/foo.md"])
        assert _extract_paths(use) == [Path("/tmp/foo.md")]

    def test_multi_path_kwarg(self):
        use = _make_use("read", kwargs={"paths": "/tmp/a.md\n/tmp/b.md"})
        result = _extract_paths(use)
        assert Path("/tmp/a.md") in result and Path("/tmp/b.md") in result

    def test_unknown_tool_returns_empty(self):
        use = _make_use("shell", args=["ls /tmp"])
        assert _extract_paths(use) == []

    def test_no_args_returns_empty(self):
        use = _make_use("read")
        assert _extract_paths(use) == []

    def test_read_batch_content(self):
        use = _make_use("read", args=[], content="/tmp/a.md\n# comment\n/tmp/b.md")
        result = _extract_paths(use)
        assert Path("/tmp/a.md") in result and Path("/tmp/b.md") in result

    def test_tilde_expansion(self):
        use = _make_use("read", kwargs={"path": "~/some-file.md"})
        result = _extract_paths(use)
        assert result and result[0].is_absolute()
        assert "~" not in str(result[0])

    def test_multi_positional_args_uses_first_only(self):
        """Multi-arg tool calls should extract only the first arg as path."""
        use = _make_use("save", args=["/tmp/target.md", "extra arg"])
        result = _extract_paths(use)
        assert result == [Path("/tmp/target.md")], (
            "only the first positional arg is a path; joining all args creates a bogus path"
        )


# ---------------------------------------------------------------------------
# Directory candidate resolution
# ---------------------------------------------------------------------------


class TestCandidateDirectories:
    def test_existing_file_uses_parent(self, tmp_path: Path):
        f = tmp_path / "foo.md"
        f.write_text("hi")
        assert _candidate_directories([f]) == [tmp_path.resolve()]

    def test_existing_directory_used_directly(self, tmp_path: Path):
        assert _candidate_directories([tmp_path]) == [tmp_path.resolve()]

    def test_nonexistent_path_uses_parent(self, tmp_path: Path):
        assert _candidate_directories([tmp_path / "ghost.md"]) == [tmp_path.resolve()]

    def test_dedups_same_directory(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.md").write_text("b")
        result = _candidate_directories([tmp_path / "a.md", tmp_path / "b.md"])
        assert result == [tmp_path.resolve()]


# ---------------------------------------------------------------------------
# End-to-end: the four design-doc scenarios
# ---------------------------------------------------------------------------


class TestOnToolExecutePost:
    def test_read_file_in_subdir_without_cd(self, tmp_path: Path, empty_log: Log):
        """Reading a file in a subdir with AGENTS.md injects those instructions."""
        subdir = tmp_path / "project"
        subdir.mkdir()
        agents = subdir / "AGENTS.md"
        agents.write_text("# Project instructions\nFollow style X.")
        target = subdir / "main.py"
        target.write_text("print('hello')")

        use = _make_use("read", kwargs={"path": str(target)})
        msgs = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )
        injected = [m for m in msgs if isinstance(m, Message)]
        assert injected, "expected AGENTS.md injection for subdir read"
        assert "Project instructions" in injected[0].content
        assert "Follow style X" in injected[0].content
        # The file is also marked loaded so re-reads stay quiet.
        assert str(agents.resolve()) in _get_loaded_files()

    def test_patch_in_worktree_without_cd(self, tmp_path: Path, empty_log: Log):
        """Patching a file in a worktree triggers the worktree's AGENTS.md."""
        worktree = tmp_path / "worktrees" / "feature"
        worktree.mkdir(parents=True)
        agents = worktree / "AGENTS.md"
        agents.write_text("# Worktree-specific rules\nNo force pushes.")
        target = worktree / "src" / "main.py"
        target.parent.mkdir()
        target.write_text("# code")

        use = _make_use("patch", kwargs={"path": str(target)})
        msgs = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )
        injected = [m for m in msgs if isinstance(m, Message)]
        assert injected, "patch in worktree should pull AGENTS.md"
        assert "Worktree-specific rules" in injected[0].content

    def test_content_hash_dedup_across_worktree_copies(
        self, tmp_path: Path, empty_log: Log
    ):
        """Identical AGENTS.md content in two paths is injected only once."""
        original = tmp_path / "repo"
        worktree = tmp_path / "worktree"
        original.mkdir()
        worktree.mkdir()
        shared = "# Shared instructions\nDo great things."
        (original / "AGENTS.md").write_text(shared)
        (worktree / "AGENTS.md").write_text(shared)

        # First touch: original repo.
        msgs1 = list(
            on_tool_execute_post(
                log=empty_log,
                workspace=tmp_path,
                tool_use=_make_use("read", kwargs={"path": str(original / "x.md")}),
            )
        )
        # Second touch: worktree copy with identical content.
        msgs2 = list(
            on_tool_execute_post(
                log=empty_log,
                workspace=tmp_path,
                tool_use=_make_use("read", kwargs={"path": str(worktree / "y.md")}),
            )
        )
        injected1 = [m for m in msgs1 if isinstance(m, Message)]
        injected2 = [m for m in msgs2 if isinstance(m, Message)]
        assert len(injected1) == 1, "original repo should inject AGENTS.md once"
        assert injected2 == [], "worktree copy with identical content must dedup"
        # Both paths are marked loaded; the content hash is recorded too.
        loaded = _get_loaded_files()
        assert str((original / "AGENTS.md").resolve()) in loaded
        assert str((worktree / "AGENTS.md").resolve()) in loaded
        assert f"{_HASH_PREFIX}{_content_hash(shared)}" in loaded

    def test_no_reinjection_on_repeated_reads(self, tmp_path: Path, empty_log: Log):
        """Reading two files in the same directory injects AGENTS.md only once."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text("# Instructions")
        (project / "a.py").write_text("# a")
        (project / "b.py").write_text("# b")

        first = list(
            on_tool_execute_post(
                log=empty_log,
                workspace=tmp_path,
                tool_use=_make_use("read", kwargs={"path": str(project / "a.py")}),
            )
        )
        second = list(
            on_tool_execute_post(
                log=empty_log,
                workspace=tmp_path,
                tool_use=_make_use("read", kwargs={"path": str(project / "b.py")}),
            )
        )
        injected1 = [m for m in first if isinstance(m, Message)]
        injected2 = [m for m in second if isinstance(m, Message)]
        assert len(injected1) == 1
        assert injected2 == []

    def test_unknown_tool_does_nothing(self, tmp_path: Path, empty_log: Log):
        """Shell and other free-form tools must not trigger path scanning."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text("# Instructions")
        use = _make_use("shell", args=[f"cat {project / 'foo.md'}"])
        msgs = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )
        assert [m for m in msgs if isinstance(m, Message)] == []

    def test_no_agents_md_means_no_injection(self, tmp_path: Path, empty_log: Log):
        """Touching a directory with no agent files is a no-op."""
        # Use a temp path well outside $HOME so the tree walk doesn't pick up
        # any ambient AGENTS.md files higher up.
        plain = tmp_path / "plain"
        plain.mkdir()
        target = plain / "readme.txt"
        target.write_text("nothing")
        use = _make_use("read", kwargs={"path": str(target)})
        msgs = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )
        injected = [
            m
            for m in msgs
            if isinstance(m, Message) and "agent-instructions" in m.content
        ]
        # Only count injections that touch our temp dir, not ambient ones.
        local_injections = [m for m in injected if str(plain) in m.content]
        assert local_injections == []

    def test_already_loaded_file_not_reinjected(self, tmp_path: Path, empty_log: Log):
        """If prompt_workspace already loaded the file, this hook skips it."""
        project = tmp_path / "project"
        project.mkdir()
        agents = project / "AGENTS.md"
        agents.write_text("# Already loaded")
        # Simulate session-start loading.
        loaded = _get_loaded_files()
        loaded.add(str(agents.resolve()))

        use = _make_use("read", kwargs={"path": str(project / "main.py")})
        msgs = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )
        local = [
            m for m in msgs if isinstance(m, Message) and str(project) in m.content
        ]
        assert local == []

    def test_read_batch_content_injects_without_cd(
        self, tmp_path: Path, empty_log: Log
    ):
        """Batch-read content paths should trigger injection just like kwargs paths."""
        project = tmp_path / "project"
        project.mkdir()
        agents = project / "AGENTS.md"
        agents.write_text("# Batch instructions\nHandle multi-read paths.")
        target = project / "a.py"
        target.write_text("print('hi')")

        use = _make_use("read", args=[], content=f"{target}\n# keep going")
        msgs = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )

        injected = [
            m
            for m in msgs
            if isinstance(m, Message) and "agent-instructions" in m.content
        ]
        assert len(injected) == 1
        assert "Batch instructions" in injected[0].content
        assert str(agents.resolve()) in _get_loaded_files()

    def test_oversized_instructions_are_skipped_but_not_marked_loaded(
        self, tmp_path: Path, empty_log: Log
    ):
        """Skipped oversized files should emit a note and remain eligible later."""
        project = tmp_path / "project"
        project.mkdir()
        agents = project / "AGENTS.md"
        agents.write_text("# Big\n" + ("x" * (_MAX_BYTES_PER_EVENT + 32)))
        target = project / "main.py"
        target.write_text("print('hi')")

        use = _make_use("read", kwargs={"path": str(target)})
        first = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )
        second = list(
            on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
        )

        for msgs in (first, second):
            skip_notes = [
                m
                for m in msgs
                if isinstance(m, Message) and "agent-instructions-skipped" in m.content
            ]
            injected = [
                m
                for m in msgs
                if isinstance(m, Message)
                and '<agent-instructions source="' in m.content
            ]
            assert len(skip_notes) == 1
            assert injected == []

        loaded = _get_loaded_files()
        assert str(agents.resolve()) not in loaded
        assert f"{_HASH_PREFIX}{_content_hash(agents.read_text())}" not in loaded

    def test_cap_prioritises_most_specific_file(self, tmp_path: Path, empty_log: Log):
        """When >MAX_INJECT_PER_EVENT files exist in the tree, the deepest (most
        project-specific) files are injected and the shallow (home-level) ones
        are dropped via a skip-note."""
        # Build a tree with more levels than the cap:
        # tmp_path/AGENTS.md  (general — should be skipped)
        # tmp_path/a/AGENTS.md
        # tmp_path/a/b/AGENTS.md
        # tmp_path/a/b/c/AGENTS.md  (most specific — must be injected)
        levels = []
        current = tmp_path
        for letter in ["a", "b", "c", "d"]:
            current = current / letter
            current.mkdir()
            agents = current / "AGENTS.md"
            agents.write_text(f"# Level {letter}")
            levels.append((letter, agents))

        # Also put one at the root so there are _MAX_INJECT_PER_EVENT + 1 total
        (tmp_path / "AGENTS.md").write_text("# Root level — should be skipped")

        deepest_dir = tmp_path / "a" / "b" / "c" / "d"
        target_file = deepest_dir / "main.py"
        target_file.write_text("x = 1")

        use = _make_use("read", kwargs={"path": str(target_file)})

        # Mock home to tmp_path so find_agent_files_in_tree walks our tree
        from unittest.mock import patch

        with patch("gptme.prompts.workspace.Path.home", return_value=tmp_path):
            msgs = list(
                on_tool_execute_post(log=empty_log, workspace=tmp_path, tool_use=use)
            )

        injected = [
            m
            for m in msgs
            if isinstance(m, Message) and '<agent-instructions source="' in m.content
        ]
        skipped = [
            m
            for m in msgs
            if isinstance(m, Message) and "agent-instructions-skipped" in m.content
        ]
        # Must inject exactly _MAX_INJECT_PER_EVENT files
        assert len(injected) == _MAX_INJECT_PER_EVENT, (
            f"expected {_MAX_INJECT_PER_EVENT} injected; got {len(injected)}"
        )
        # The deepest file must be among the injected ones
        assert any("Level d" in m.content for m in injected), (
            "most-specific (deepest) AGENTS.md must be injected when cap hits"
        )
        # The root-level general file must NOT be in the injected content
        assert not any("Root level" in m.content for m in injected), (
            "general root-level AGENTS.md must be dropped when cap hits, not injected"
        )
        # At least one skip-note should have been emitted (for the dropped files)
        assert skipped, "expected skip-notes for files beyond the cap"
