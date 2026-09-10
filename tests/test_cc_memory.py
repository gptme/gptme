"""Tests for Claude Code memory integration in gptme workspace context."""

import re
import textwrap
from pathlib import Path
from unittest.mock import patch

from gptme.dirs import _claude_project_dirname, get_cc_memory_dir, get_cc_memory_file
from gptme.memory import MemoryRoot


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


def _make_entry(
    memory_dir: Path, name: str, description: str, body: str = "", type: str = "project"
) -> Path:
    """Write a minimal CC-compatible memory entry file and return its path."""
    slug = name.replace(" ", "-").lower()
    path = memory_dir / f"{slug}.md"
    content = textwrap.dedent(f"""\
        ---
        name: {slug}
        description: "{description}"
        metadata:
          type: {type}
        ---

        {body or description}
    """)
    path.write_text(content)
    return path


def _common_patches(mock_roots=None):
    """Return context manager stack for workspace prompt unit tests.

    Patches out external I/O (git, config) and optionally mocks resolve_roots
    to return a controlled set of MemoryRoot objects.
    """
    patches = [
        patch("gptme.prompts.workspace.get_config"),
        patch("gptme.prompts.workspace.get_project_config", return_value=None),
        patch("gptme.prompts.workspace.get_tree_output", return_value=None),
        patch("gptme.prompts.workspace._get_git_status", return_value=None),
        patch("gptme.prompts.workspace.find_agent_files_in_tree", return_value=[]),
    ]
    if mock_roots is not None:
        patches.insert(
            0, patch("gptme.prompts.workspace.resolve_roots", return_value=mock_roots)
        )
    return patches


class TestCcMemoryInWorkspacePrompt:
    """Tests for layered memory loading in prompt_workspace (gptme/gptme#3625)."""

    def test_loads_cc_memory_when_present(self, tmp_path):
        """CC-scoped memory entries are included in workspace context."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "cc_memory"
        memory_dir.mkdir()
        _make_entry(memory_dir, "key-insight", "Key insight about this project")

        cc_root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[cc_root]),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" in combined
        assert "key-insight" in combined

    def test_loads_from_project_memory_dir(self, tmp_path):
        """Project-scoped memory/ entries are auto-loaded into workspace context.

        This is the core feature of #3625: memories written by any harness to
        the project memory/ dir become visible in the next gptme session.
        """
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = workspace / "memory"
        memory_dir.mkdir()
        _make_entry(memory_dir, "project-fact", "A key project fact", type="project")

        project_root = MemoryRoot("project", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[project_root]),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" in combined
        assert "project-fact" in combined

    def test_cross_harness_both_roots_visible(self, tmp_path):
        """Memories from project and CC roots both appear in a single session.

        This verifies the cross-harness round-trip: CC writes to cc root, gptme
        writes to project root, both are visible in a subsequent gptme session.
        """
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()

        # Simulate CC writing to its root
        cc_dir = tmp_path / "cc_root"
        cc_dir.mkdir()
        _make_entry(cc_dir, "cc-fact", "Written by Claude Code", type="feedback")

        # Simulate gptme writing to project root
        proj_dir = workspace / "memory"
        proj_dir.mkdir()
        _make_entry(proj_dir, "gptme-fact", "Written by gptme", type="project")

        roots = [MemoryRoot("project", proj_dir), MemoryRoot("cc", cc_dir)]

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=roots),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" in combined
        # Both harnesses' entries are present
        assert "cc-fact" in combined
        assert "gptme-fact" in combined

    def test_mixed_roots_entry_files_and_legacy_memory_md(self, tmp_path):
        """A root with per-entry files and a root with only MEMORY.md are both shown.

        Regression test: before the per-root fallback, when any root had entry
        files the combined `entries` list was non-empty and the else-branch
        (MEMORY.md fallback) was skipped entirely, silently dropping the legacy
        root's content. The fix evaluates each root independently.
        """
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()

        # Root A: modern per-entry files (e.g. gptme project root)
        proj_dir = workspace / "memory"
        proj_dir.mkdir()
        _make_entry(proj_dir, "gptme-fact", "Written by gptme", type="project")

        # Root B: legacy CC root with only MEMORY.md, no individual entry files
        cc_dir = tmp_path / "cc_root"
        cc_dir.mkdir()
        (cc_dir / "MEMORY.md").write_text(
            "# Persistent Memory\n\n- [legacy-note](legacy-note.md) — CC legacy memory\n"
        )

        roots = [MemoryRoot("project", proj_dir), MemoryRoot("cc", cc_dir)]

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=roots),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" in combined
        # Per-entry root content is present
        assert "gptme-fact" in combined
        # Legacy MEMORY.md root content is also present — not dropped
        assert "legacy-note" in combined or "CC legacy memory" in combined

    def test_mixed_root_entries_and_memory_md_both_shown(self, tmp_path):
        """An unmanaged MEMORY.md and entries missing from it are both shown."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()

        # A single cc root that has BOTH per-entry files (gptme-written) AND a
        # MEMORY.md (Claude Code-written content not yet represented as entries).
        cc_dir = tmp_path / "cc_root"
        cc_dir.mkdir()
        _make_entry(cc_dir, "gptme-fact", "A gptme-written entry", type="user")
        # Simulate CC writing its own memories to MEMORY.md in the same directory
        (cc_dir / "MEMORY.md").write_text(
            "# Persistent Memory\n\n- [cc-note](cc-note.md) — CC-written memory note\n"
        )

        cc_root = MemoryRoot("cc", cc_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[cc_root]),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" in combined
        # gptme entries are visible
        assert "gptme-fact" in combined
        # CC-written MEMORY.md content is also visible — not silently dropped
        assert "cc-note" in combined or "CC-written memory note" in combined

    def test_unmanaged_memory_md_does_not_duplicate_entry_pointers(self, tmp_path):
        """Equivalent relative links keep an unmanaged pointer authoritative."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        for name in (
            "plain",
            "dot-relative",
            "angled",
            "anchored",
            "subdirectory",
            "external",
            "missing",
        ):
            _make_entry(memory_dir, name, f"Entry {name}", type="user")
        (memory_dir / "MEMORY.md").write_text(
            "# Persistent Memory\n\n"
            "- [plain](plain.md) — plain link\n"
            "- [dot](./dot-relative.md) — dot-relative link\n"
            "- [angle](<angled.md>) — angle-delimited link\n"
            "- [anchor](anchored.md#detail) — link with an anchor\n"
            "- [nested](nested/subdirectory.md) — different file in a subdirectory\n"
            "- [external](https://example.com/external.md) — unrelated URL\n"
            "\nOperator guidance that is not represented by an entry.\n"
        )
        root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[root]),
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

        combined = "\n".join(m.content for m in messages)
        generated_index = combined.rsplit("# Persistent Memory", 1)[-1]
        for name in ("plain", "dot-relative", "angled", "anchored"):
            assert f"]({name}.md)" not in generated_index
        # The URL does not point at the local entry despite sharing its basename:
        # both the external URL and a generated local pointer must be present.
        assert "nested/subdirectory.md" in combined
        assert "](subdirectory.md)" in generated_index
        assert combined.count("subdirectory.md") == 2
        assert "https://example.com/external.md" in combined
        assert "](external.md)" in generated_index
        assert combined.count("external.md") == 2
        assert "](missing.md)" in generated_index
        assert "Operator guidance" in combined

    def test_parenthesized_markdown_target_does_not_hide_local_entry(self, tmp_path):
        """A partial parse of a complex target must not suppress an entry."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        _make_entry(memory_dir, "a-b", "Local entry", type="user")
        (memory_dir / "MEMORY.md").write_text(
            "- [complex](<a(b).md>) — unrelated complex Markdown target\n"
        )
        root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[root]),
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

        combined = "\n".join(m.content for m in messages)
        assert "<a(b).md>" in combined
        assert "](a-b.md)" in combined

    def test_truncated_unmanaged_index_does_not_append_duplicate_pointers(
        self, tmp_path
    ):
        """A partial compatibility index consumes the remaining root budget."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        _make_entry(memory_dir, "linked-late", "Entry linked late", type="user")
        (memory_dir / "MEMORY.md").write_text(
            "legacy content that fills most of the budget\n- [late](linked-late.md)\n"
        )
        root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[root]),
            patch("gptme.prompts.workspace.get_config") as mock_config,
            patch("gptme.prompts.workspace.get_project_config", return_value=None),
            patch("gptme.prompts.workspace.get_tree_output", return_value=None),
            patch("gptme.prompts.workspace._get_git_status", return_value=None),
            patch("gptme.prompts.workspace.find_agent_files_in_tree", return_value=[]),
            patch("gptme.prompts.workspace._MEMORY_BUDGET_BYTES", 50),
        ):
            mock_config.return_value.user = None
            messages = list(
                prompt_workspace(
                    workspace=workspace,
                    include_user_context=True,
                    include_context_cmd=False,
                )
            )

        combined = "\n".join(m.content for m in messages)
        assert "legacy content" in combined
        assert "[late](linked-late.md)" not in combined
        assert combined.count("linked-late.md") == 0
        memory_message = next(
            message
            for message in messages
            if message.content.startswith("## Persistent Memory")
        )
        payload = memory_message.content.split("):\n\n", 1)[1]
        assert len(payload.encode("utf-8")) <= 50

    def test_no_memory_when_no_roots_exist(self, tmp_path):
        """No memory message is emitted when no memory roots have files."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        # No roots — resolve_roots returns empty list

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[]),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" not in combined

    def test_skips_memory_when_include_user_context_false(self, tmp_path):
        """Layered memory is not loaded when include_user_context=False (e.g. eval mode)."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        _make_entry(memory_dir, "some-fact", "Some insight")
        root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[root]),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" not in combined

    def test_falls_back_to_memory_md_when_no_entry_files(self, tmp_path):
        """A CC root with only MEMORY.md (no individual entry files) still shows
        its content via the legacy fallback path.

        Regression test: the layered-store path (MemoryStore.entries()) skips
        MEMORY.md by design; without the fallback, existing CC memories written
        by older harnesses or hand-authored indexes are silently dropped.
        """
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # Only MEMORY.md — no individual entry files (legacy CC format)
        (memory_dir / "MEMORY.md").write_text(
            "# Persistent Memory\n\n- [my-note](my-note.md) — a legacy memory\n"
        )

        root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[root]),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" in combined
        assert "legacy memory" in combined

    def test_skips_dir_with_empty_memory_md(self, tmp_path):
        """A memory dir whose MEMORY.md contains only whitespace produces no output."""
        from gptme.prompts.workspace import prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        # Blank MEMORY.md and no individual entry files
        (memory_dir / "MEMORY.md").write_text("\n\n")

        root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[root]),
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

        combined = "\n".join(m.content for m in messages)
        assert "Persistent Memory" not in combined

    def test_budget_limits_injected_content(self, tmp_path):
        """render_index budget caps the memory block to _MEMORY_BUDGET_BYTES."""
        from gptme.prompts.workspace import _MEMORY_BUDGET_BYTES, prompt_workspace

        workspace = tmp_path / "myproject"
        workspace.mkdir()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        # The rendered index contains descriptions, not bodies. Make their combined
        # size exceed the 64 KB cap so the omission path must execute.
        long_desc = "x" * 4000
        for i in range(20):
            _make_entry(
                memory_dir,
                f"big-entry-{i:02d}",
                long_desc,
                body="",
            )

        root = MemoryRoot("cc", memory_dir)

        with (
            patch("gptme.prompts.workspace.resolve_roots", return_value=[root]),
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
        # The rendered index (not individual entries) is included — it should be bounded,
        # and the omission marker proves this is not a trivially undersized fixture.
        injected_bytes = len(memory_msgs[0].content.encode("utf-8"))
        assert "omitted" in memory_msgs[0].content.lower()
        assert injected_bytes <= _MEMORY_BUDGET_BYTES + 512  # header overhead allowed
