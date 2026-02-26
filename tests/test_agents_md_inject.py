"""Tests for the AGENTS.md injection hook (gptme/hooks/agents_md_inject.py).

Tests that agent instruction files (AGENTS.md, CLAUDE.md, GEMINI.md) are
automatically injected as system messages when the working directory changes.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gptme.hooks.agents_md_inject import (
    _find_agent_files_in_tree,
    _loaded_agent_files,
    post_execute,
    pre_execute,
    session_start_seed,
)
from gptme.message import Message


def _messages_only(items: list) -> list[Message]:
    """Filter hook results to only Message objects (exclude StopPropagation)."""
    return [m for m in items if isinstance(m, Message)]


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset module state between tests."""
    _loaded_agent_files.clear()
    yield
    _loaded_agent_files.clear()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace directory with an AGENTS.md file."""
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("# Workspace Instructions\n\nDo things.\n")
    return tmp_path


@pytest.fixture
def subdir_with_agents(workspace: Path) -> Path:
    """Create a subdirectory with its own AGENTS.md."""
    subdir = workspace / "subproject"
    subdir.mkdir()
    agents_file = subdir / "AGENTS.md"
    agents_file.write_text("# Subproject Instructions\n\nDo subproject things.\n")
    return subdir


@pytest.fixture
def subdir_with_claude_md(workspace: Path) -> Path:
    """Create a subdirectory with CLAUDE.md."""
    subdir = workspace / "claude-project"
    subdir.mkdir()
    claude_file = subdir / "CLAUDE.md"
    claude_file.write_text("# Claude Instructions\n\nClaude-specific rules.\n")
    return subdir


class TestFindAgentFiles:
    """Test _find_agent_files_in_tree()."""

    def test_finds_agents_md(self, workspace: Path):
        """Should find AGENTS.md in the given directory."""
        files = _find_agent_files_in_tree(workspace)
        resolved = [str(f.resolve()) for f in files]
        assert str((workspace / "AGENTS.md").resolve()) in resolved

    def test_skips_already_loaded(self, workspace: Path):
        """Should not return files already in _loaded_agent_files."""
        _loaded_agent_files.add(str((workspace / "AGENTS.md").resolve()))
        files = _find_agent_files_in_tree(workspace)
        resolved = [str(f.resolve()) for f in files]
        assert str((workspace / "AGENTS.md").resolve()) not in resolved

    def test_finds_in_subdirectory(self, subdir_with_agents: Path):
        """Should find AGENTS.md in subdirectory."""
        files = _find_agent_files_in_tree(subdir_with_agents)
        resolved = [str(f.resolve()) for f in files]
        assert str((subdir_with_agents / "AGENTS.md").resolve()) in resolved

    def test_finds_claude_md(self, subdir_with_claude_md: Path):
        """Should find CLAUDE.md as well as AGENTS.md."""
        files = _find_agent_files_in_tree(subdir_with_claude_md)
        resolved = [str(f.resolve()) for f in files]
        assert str((subdir_with_claude_md / "CLAUDE.md").resolve()) in resolved

    def test_finds_parent_and_child(self, workspace: Path, subdir_with_agents: Path):
        """Should find both parent workspace AGENTS.md and subdir AGENTS.md."""
        files = _find_agent_files_in_tree(subdir_with_agents)
        resolved = [str(f.resolve()) for f in files]
        # Both parent and child should be found (neither loaded yet)
        assert str((workspace / "AGENTS.md").resolve()) in resolved
        assert str((subdir_with_agents / "AGENTS.md").resolve()) in resolved

    def test_no_files_in_empty_dir(self, tmp_path: Path):
        """Should return empty list when no agent files exist."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        files = _find_agent_files_in_tree(empty_dir)
        # Filter to only files within tmp_path (ignore home dir files)
        local_files = [
            f for f in files if str(f.resolve()).startswith(str(tmp_path.resolve()))
        ]
        assert local_files == []


class TestSessionStartSeed:
    """Test session_start_seed() — seeding loaded files from initial workspace."""

    def test_seeds_workspace_agents_md(self, workspace: Path):
        """Should add workspace AGENTS.md to loaded set."""
        msgs = list(
            session_start_seed(
                logdir=workspace / ".gptme",
                workspace=workspace,
                initial_msgs=[],
            )
        )
        assert msgs == []  # No messages yielded (just seeding)
        assert str((workspace / "AGENTS.md").resolve()) in _loaded_agent_files

    def test_seeds_nothing_when_no_workspace(self):
        """Should be a no-op when workspace is None."""
        msgs = list(
            session_start_seed(
                logdir=Path("/tmp"),
                workspace=None,
                initial_msgs=[],
            )
        )
        assert msgs == []

    def test_seeds_both_parent_and_subdir(
        self, workspace: Path, subdir_with_agents: Path
    ):
        """When workspace is the subdir, both parent and child are seeded."""
        list(
            session_start_seed(
                logdir=subdir_with_agents / ".gptme",
                workspace=subdir_with_agents,
                initial_msgs=[],
            )
        )
        assert str((workspace / "AGENTS.md").resolve()) in _loaded_agent_files
        assert str((subdir_with_agents / "AGENTS.md").resolve()) in _loaded_agent_files


class TestPostExecuteInjection:
    """Test the post_execute hook — injecting AGENTS.md on CWD change."""

    def test_injects_on_cd(self, workspace: Path, subdir_with_agents: Path):
        """Should inject AGENTS.md content when CWD changes to dir with new file."""
        # Seed loaded files for workspace
        _loaded_agent_files.add(str((workspace / "AGENTS.md").resolve()))

        # Simulate: CWD was workspace, now is subdir
        orig_cwd = os.getcwd()
        try:
            os.chdir(workspace)
            # Pre-execute stores CWD
            list(
                pre_execute(log=MagicMock(), workspace=workspace, tool_use=MagicMock())
            )

            # Change to subdir (simulating cd)
            os.chdir(subdir_with_agents)

            # Post-execute should detect change and inject
            msgs = _messages_only(
                list(
                    post_execute(
                        log=MagicMock(), workspace=workspace, tool_use=MagicMock()
                    )
                )
            )

            assert len(msgs) == 1
            assert "Subproject Instructions" in msgs[0].content
            assert "agent-instructions" in msgs[0].content
            # File should now be tracked
            assert (
                str((subdir_with_agents / "AGENTS.md").resolve()) in _loaded_agent_files
            )
        finally:
            os.chdir(orig_cwd)

    def test_no_inject_when_no_change(self, workspace: Path):
        """Should not inject anything when CWD hasn't changed."""
        _loaded_agent_files.add(str((workspace / "AGENTS.md").resolve()))

        orig_cwd = os.getcwd()
        try:
            os.chdir(workspace)
            list(
                pre_execute(log=MagicMock(), workspace=workspace, tool_use=MagicMock())
            )
            # CWD didn't change
            msgs = list(
                post_execute(log=MagicMock(), workspace=workspace, tool_use=MagicMock())
            )
            assert msgs == []
        finally:
            os.chdir(orig_cwd)

    def test_no_inject_when_already_loaded(
        self, workspace: Path, subdir_with_agents: Path
    ):
        """Should not re-inject files already in the loaded set."""
        _loaded_agent_files.add(str((workspace / "AGENTS.md").resolve()))
        _loaded_agent_files.add(str((subdir_with_agents / "AGENTS.md").resolve()))

        orig_cwd = os.getcwd()
        try:
            os.chdir(workspace)
            list(
                pre_execute(log=MagicMock(), workspace=workspace, tool_use=MagicMock())
            )
            os.chdir(subdir_with_agents)
            msgs = list(
                post_execute(log=MagicMock(), workspace=workspace, tool_use=MagicMock())
            )
            assert msgs == []
        finally:
            os.chdir(orig_cwd)

    def test_injects_claude_md(self, workspace: Path, subdir_with_claude_md: Path):
        """Should inject CLAUDE.md when found in new directory."""
        _loaded_agent_files.add(str((workspace / "AGENTS.md").resolve()))

        orig_cwd = os.getcwd()
        try:
            os.chdir(workspace)
            list(
                pre_execute(log=MagicMock(), workspace=workspace, tool_use=MagicMock())
            )
            os.chdir(subdir_with_claude_md)
            msgs = _messages_only(
                list(
                    post_execute(
                        log=MagicMock(), workspace=workspace, tool_use=MagicMock()
                    )
                )
            )
            assert len(msgs) == 1
            assert "Claude Instructions" in msgs[0].content
        finally:
            os.chdir(orig_cwd)

    def test_injects_multiple_files(self, workspace: Path):
        """Should inject both AGENTS.md and CLAUDE.md from same directory."""
        subdir = workspace / "multi"
        subdir.mkdir()
        (subdir / "AGENTS.md").write_text("# Multi Agents\n")
        (subdir / "CLAUDE.md").write_text("# Multi Claude\n")

        _loaded_agent_files.add(str((workspace / "AGENTS.md").resolve()))

        orig_cwd = os.getcwd()
        try:
            os.chdir(workspace)
            list(
                pre_execute(log=MagicMock(), workspace=workspace, tool_use=MagicMock())
            )
            os.chdir(subdir)
            msgs = _messages_only(
                list(
                    post_execute(
                        log=MagicMock(), workspace=workspace, tool_use=MagicMock()
                    )
                )
            )
            assert len(msgs) == 2
            contents = [m.content for m in msgs]
            assert any("Multi Agents" in c for c in contents)
            assert any("Multi Claude" in c for c in contents)
        finally:
            os.chdir(orig_cwd)
