"""Tests for Claude Code memory integration in gptme workspace context."""

import re
from pathlib import Path
from unittest.mock import patch

from gptme.dirs import _claude_project_dirname, get_cc_memory_dir, get_cc_memory_file


class TestClaudeProjectDirname:
    """Tests for _claude_project_dirname (CC's cwd → project-dir encoding).

    Expected values cross-checked against Claude Code's own implementation
    (cli.js, v2.1.239): sanitize = s.replace(/[^a-zA-Z0-9]/g, "-"), with a
    truncate-to-200 + base36-hash fallback for overlong names.

    The hash suffixes below were produced by running CC's own functions in
    Node; regenerate with (charCodeAt is per UTF-16 unit, like CC)::

        node -e 'const wv=e=>e.replace(/[^a-zA-Z0-9]/g,"-");
          const y9t=e=>{let t=0;for(let r=0;r<e.length;r++)t=(t<<5)-t+e.charCodeAt(r)|0;
            return Math.abs(t).toString(36)};
          const pL=e=>{const t=wv(e);return t.length<=200?t:t.slice(0,200)+"-"+y9t(e)};
          console.log(pL(process.argv[1]))' "<path>"
    """

    def test_posix_path(self):
        assert _claude_project_dirname("/home/user/myproject") == "-home-user-myproject"

    def test_empty_path(self):
        # struct.unpack("<0H", b"") -> (); no units -> empty dirname (matches JS)
        assert _claude_project_dirname("") == ""

    def test_windows_path(self):
        assert (
            _claude_project_dirname("C:\\Users\\user\\project")
            == "C--Users-user-project"
        )

    def test_underscores_dots_spaces(self):
        assert (
            _claude_project_dirname("/home/user/my_project.v2 backup")
            == "-home-user-my-project-v2-backup"
        )

    def test_runs_not_collapsed(self):
        # C:\ -> "C" + ":" + "\" -> "C--"
        assert _claude_project_dirname("C:\\").startswith("C--")
        assert _claude_project_dirname("/a/./b") == "-a---b"

    def test_non_ascii_becomes_dashes(self):
        # BMP non-alphanumeric -> one dash per code unit
        assert _claude_project_dirname("/tmp/café") == "-tmp-caf-"

    def test_astral_char_is_two_dashes(self):
        # CC runs on JS: an astral char is two UTF-16 code units, so it
        # sanitizes to "--" and counts as length 2. Node reference:
        # "/home/user/\U0001F600/proj" -> "-home-user----proj"
        assert (
            _claude_project_dirname("/home/user/\U0001f600/proj")
            == "-home-user----proj"
        )

    def test_overlong_path_truncated_and_hashed(self):
        path = "/" + "a_very/deep.path " * 30 + "end"
        result = _claude_project_dirname(path)
        # CC: slice(0, 200) + "-" + hash; node reference gives hash "xs0et0"
        assert len(result) == 207
        assert result.endswith("-xs0et0")
        assert result[:200] == re.sub(r"[^a-zA-Z0-9]", "-", path)[:200]

    def test_overlong_path_with_astral_char(self):
        # Node reference: "/a/🚀🚀/b" + "y"*210 -> hash "jga4eu"
        path = "/a/\U0001f680\U0001f680/b" + "y" * 210
        result = _claude_project_dirname(path)
        assert result.endswith("-jga4eu")
        assert result[:200].startswith("-a------b")


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

    def test_non_alphanumeric_replaced(self):
        """Every non-alphanumeric char is replaced by a dash, matching CC's encoding.

        CC encodes the cwd as ``cwd.replace(/[^a-zA-Z0-9]/g, '-')``, so
        underscores, dots and spaces must map to dashes too — not just path
        separators. e.g. ``/home/user/my_project`` → ``-home-user-my-project``.
        Patched resolve() keeps this independent of the host OS.
        """

        class _Resolved:
            def __str__(self):
                return "/home/user/my_project.v2 backup"

        with patch.object(Path, "resolve", return_value=_Resolved()):
            hash_part = get_cc_memory_dir(Path("/irrelevant")).parent.name
        assert hash_part == "-home-user-my-project-v2-backup"

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

    def test_windows_backslashes_replaced(self):
        """Windows-style backslashes in resolved path strings are normalised to dashes.

        On Windows, str(Path.resolve()) returns backslash separators. The function
        must replace them so the resulting hash component contains no backslashes.
        """

        class _WindowsPath:
            def __str__(self):
                return "C:\\Users\\user\\myproject"

        workspace = Path("/irrelevant")
        with patch.object(Path, "resolve", return_value=_WindowsPath()):
            cc_dir = get_cc_memory_dir(workspace)

        hash_part = cc_dir.parent.name  # the workspace_hash component
        assert "\\" not in hash_part
        assert ":" not in hash_part
        assert hash_part == "C--Users-user-myproject"

    def test_path_collision_documented(self):
        """Paths differing only by dash-vs-separator produce the same hash (CC's design).

        e.g. /a/b, /a-b and /a_b all map to '-a-b'. This is an inherent property
        of CC's own encoding (every non-alphanumeric char → dash); gptme
        replicates it faithfully.
        """
        ws_slash = Path("/home/user/a/b")
        ws_dash = Path("/home/user/a-b")
        ws_underscore = Path("/home/user/a_b")
        assert get_cc_memory_dir(ws_slash) == get_cc_memory_dir(ws_dash)
        assert get_cc_memory_dir(ws_slash) == get_cc_memory_dir(ws_underscore)


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

    def test_non_utf8_memory_drops_invalid_bytes(self, tmp_path):
        """Non-UTF-8 bytes in MEMORY.md are silently dropped (errors='ignore').

        Using errors='replace' would expand each invalid byte to 3-byte U+FFFD,
        allowing 64KB of input to produce ~192KB of decoded text — exceeding the
        intended size cap. errors='ignore' keeps the decoded size bounded.
        """
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        cc_memory_file = tmp_path / "MEMORY.md"
        # Latin-1 encoded text: "café" — 0xe9 is invalid UTF-8 lead byte
        cc_memory_file.write_bytes(b"# Memory\n\ncaf\xe9\n")

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

        memory_msgs = [m for m in messages if "Persistent Memory" in m.content]
        assert len(memory_msgs) == 1
        content = memory_msgs[0].content
        # The invalid byte is dropped; valid prefix survives
        assert "caf" in content
        # No U+FFFD replacement chars (that would indicate errors='replace')
        assert "�" not in content

    def test_oversized_memory_is_truncated(self, tmp_path):
        """Memory files exceeding the size cap are truncated before injection.

        Critically, the file must NOT be fully read — only _CC_MEMORY_MAX_BYTES+1
        bytes should be consumed so that multi-MB MEMORY.md files cannot stall
        prompt construction.
        """
        from gptme.prompts.workspace import _CC_MEMORY_MAX_BYTES, prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        cc_memory_file = tmp_path / "MEMORY.md"
        big_content = "# Memory\n\n" + ("x" * (_CC_MEMORY_MAX_BYTES + 10_000))
        cc_memory_file.write_text(big_content, encoding="utf-8")

        max_bytes_read = []

        real_open = open

        def tracking_open(path, mode="r", **kw):
            fh = real_open(path, mode, **kw)
            if str(path) == str(cc_memory_file) and "b" in mode:
                real_read = fh.read

                def bounded_read(n=-1):
                    data = real_read(n)
                    max_bytes_read.append(len(data))
                    return data

                fh.read = bounded_read
            return fh

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
            patch("builtins.open", side_effect=tracking_open),
        ):
            mock_config.return_value.user = None
            messages = list(
                prompt_workspace(
                    workspace=workspace,
                    include_user_context=True,
                    include_context_cmd=False,
                )
            )

        memory_msgs = [m for m in messages if "Persistent Memory" in m.content]
        assert len(memory_msgs) == 1
        injected = memory_msgs[0].content.encode("utf-8")
        # Output is bounded
        assert len(injected) <= _CC_MEMORY_MAX_BYTES * 2
        # The file was read with a size bound, not in full
        assert max_bytes_read, "open() was not called on the memory file in binary mode"
        assert max(max_bytes_read) <= _CC_MEMORY_MAX_BYTES + 1
