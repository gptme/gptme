"""Tests for Claude Code memory integration in gptme workspace context."""

from pathlib import Path
from unittest.mock import patch

from gptme.dirs import get_cc_memory_dir, get_cc_memory_file


class TestGetCcMemoryDir:
    """Tests for get_cc_memory_dir."""

    def test_path_formula(self, tmp_path):
        """CC memory dir uses workspace path with slashes replaced by dashes."""
        workspace = Path("/home/user/myproject")
        cc_dir = get_cc_memory_dir(workspace)
        assert (
            cc_dir
            == Path.home() / ".claude" / "projects" / "-home-user-myproject" / "memory"
        )

    def test_nested_workspace(self, tmp_path):
        """Deeper workspace paths produce correct hash."""
        workspace = Path("/home/alice/code/myorg/myrepo")
        cc_dir = get_cc_memory_dir(workspace)
        expected_hash = "-home-alice-code-myorg-myrepo"
        assert cc_dir.name == "memory"
        assert cc_dir.parent.name == expected_hash

    def test_resolves_workspace(self, tmp_path):
        """Workspace is resolved to absolute before hashing."""
        # tmp_path is already absolute; create a symlink to test resolve
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)
        cc_dir_real = get_cc_memory_dir(real_dir)
        cc_dir_link = get_cc_memory_dir(link_dir)
        # Both should resolve to the same hash
        assert cc_dir_real == cc_dir_link


class TestGetCcMemoryFile:
    """Tests for get_cc_memory_file."""

    def test_returns_memory_md(self):
        """Returns MEMORY.md inside the CC memory dir."""
        workspace = Path("/home/user/myproject")
        cc_file = get_cc_memory_file(workspace)
        assert cc_file.name == "MEMORY.md"
        assert cc_file.parent == get_cc_memory_dir(workspace)


class TestCcMemoryInWorkspacePrompt:
    """Tests for CC memory loading in prompt_workspace."""

    def test_loads_cc_memory_when_present(self, tmp_path):
        """CC memory is included in workspace context when MEMORY.md exists."""
        from gptme.prompts.workspace import prompt_workspace

        # Create a fake CC memory file
        workspace = tmp_path / "myproject"
        workspace.mkdir()
        workspace_hash = str(workspace.resolve()).replace("/", "-")
        cc_memory_dir = (
            tmp_path / "home" / ".claude" / "projects" / workspace_hash / "memory"
        )
        cc_memory_dir.mkdir(parents=True)
        cc_memory_file = cc_memory_dir / "MEMORY.md"
        cc_memory_file.write_text("# Memory\n\n- Key insight about this project\n")

        with (
            patch(
                "gptme.prompts.workspace.get_cc_memory_file",
                return_value=cc_memory_file,
            ),
            patch("gptme.prompts.workspace.get_config") as mock_config,
            patch("gptme.prompts.workspace.get_project_config", return_value=None),
            patch("gptme.prompts.workspace.get_tree_output", return_value=None),
            patch("gptme.prompts.workspace._get_git_status", return_value=None),
            patch("gptme.prompts.workspace.find_agent_files_in_tree", return_value=[]),
        ):
            mock_config.return_value.user = None
            messages = list(
                prompt_workspace(
                    workspace=workspace,
                    include_user_context=True,
                    include_context_cmd=False,
                )
            )

        contents = [m.content for m in messages]
        combined = "\n".join(contents)
        assert "Persistent Memory" in combined
        assert "Key insight about this project" in combined

    def test_no_memory_when_file_missing(self, tmp_path):
        """No memory message is emitted when CC MEMORY.md doesn't exist."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        nonexistent = tmp_path / "nonexistent" / "MEMORY.md"

        with (
            patch(
                "gptme.prompts.workspace.get_cc_memory_file", return_value=nonexistent
            ),
            patch("gptme.prompts.workspace.get_config") as mock_config,
            patch("gptme.prompts.workspace.get_project_config", return_value=None),
            patch("gptme.prompts.workspace.get_tree_output", return_value=None),
            patch("gptme.prompts.workspace._get_git_status", return_value=None),
            patch("gptme.prompts.workspace.find_agent_files_in_tree", return_value=[]),
        ):
            mock_config.return_value.user = None
            messages = list(
                prompt_workspace(
                    workspace=workspace,
                    include_user_context=True,
                    include_context_cmd=False,
                )
            )

        contents = [m.content for m in messages]
        combined = "\n".join(contents)
        assert "Persistent Memory" not in combined

    def test_skips_memory_when_include_user_context_false(self, tmp_path):
        """CC memory is not loaded when include_user_context=False (e.g. eval mode)."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        cc_memory_file = tmp_path / "MEMORY.md"
        cc_memory_file.write_text("# Memory\n\n- Some insight\n")

        with (
            patch(
                "gptme.prompts.workspace.get_cc_memory_file",
                return_value=cc_memory_file,
            ),
            patch("gptme.prompts.workspace.get_config") as mock_config,
            patch("gptme.prompts.workspace.get_project_config", return_value=None),
            patch("gptme.prompts.workspace.get_tree_output", return_value=None),
            patch("gptme.prompts.workspace._get_git_status", return_value=None),
            patch("gptme.prompts.workspace.find_agent_files_in_tree", return_value=[]),
        ):
            mock_config.return_value.user = None
            messages = list(
                prompt_workspace(
                    workspace=workspace,
                    include_user_context=False,
                    include_context_cmd=False,
                )
            )

        contents = [m.content for m in messages]
        combined = "\n".join(contents)
        assert "Persistent Memory" not in combined

    def test_skips_empty_memory_file(self, tmp_path):
        """Empty MEMORY.md produces no memory message."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        cc_memory_file = tmp_path / "MEMORY.md"
        cc_memory_file.write_text("   \n   ")  # whitespace only

        with (
            patch(
                "gptme.prompts.workspace.get_cc_memory_file",
                return_value=cc_memory_file,
            ),
            patch("gptme.prompts.workspace.get_config") as mock_config,
            patch("gptme.prompts.workspace.get_project_config", return_value=None),
            patch("gptme.prompts.workspace.get_tree_output", return_value=None),
            patch("gptme.prompts.workspace._get_git_status", return_value=None),
            patch("gptme.prompts.workspace.find_agent_files_in_tree", return_value=[]),
        ):
            mock_config.return_value.user = None
            messages = list(
                prompt_workspace(
                    workspace=workspace,
                    include_user_context=True,
                    include_context_cmd=False,
                )
            )

        contents = [m.content for m in messages]
        combined = "\n".join(contents)
        assert "Persistent Memory" not in combined
