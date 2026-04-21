import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from gptme.message import len_tokens
from gptme.prompts import get_prompt
from gptme.tools import get_tools, init_tools


@pytest.fixture(autouse=True)
def init():
    init_tools()


# Extra allowed tokens for user config
user_config_size = 2000 if "CI" not in os.environ else 0


def test_get_prompt_full():
    prompt_msgs = get_prompt(get_tools(), prompt="full")
    # Combine all message contents for token counting
    combined_content = "\n\n".join(msg.content for msg in prompt_msgs)

    # TODO: lower this significantly by selectively removing examples from the full prompt
    # Note: Hook system documentation increased the prompt size, should optimize later
    assert 500 < len_tokens(combined_content, "gpt-4") < 8000 + user_config_size


def test_get_prompt_short():
    prompt_msgs = get_prompt(get_tools(), prompt="short")
    # Combine all message contents for token counting
    combined_content = "\n\n".join(msg.content for msg in prompt_msgs)

    # TODO: make the short prompt shorter
    # Note: Lesson system additions increased prompt size slightly
    assert 500 < len_tokens(combined_content, "gpt-4") < 4000 + user_config_size


def test_get_prompt_custom():
    prompt_msgs = get_prompt([], prompt="Hello world!")
    assert len(prompt_msgs) == 1
    assert prompt_msgs[0].content == "Hello world!"


def test_get_prompt_selective_tools_always_included():
    """Test that tool descriptions are always included when tools are loaded."""
    # Tools loaded: descriptions should be in prompt regardless of context_include
    with_tools_flag = get_prompt(
        get_tools(),
        prompt="full",
        context_mode="selective",
        context_include=["tools"],  # explicit (legacy, still works)
    )
    without_tools_flag = get_prompt(
        get_tools(),
        prompt="full",
        context_mode="selective",
        context_include=[],
    )

    with_content = "\n\n".join(msg.content for msg in with_tools_flag)
    without_content = "\n\n".join(msg.content for msg in without_tools_flag)

    # Should be the same size — tools are always included when loaded
    assert len_tokens(with_content, "gpt-4") == len_tokens(without_content, "gpt-4")

    # No tools loaded: should have less content
    no_tools = get_prompt(
        [],
        prompt="full",
        context_mode="selective",
        context_include=[],
    )
    no_tools_content = "\n\n".join(msg.content for msg in no_tools)
    assert len_tokens(no_tools_content, "gpt-4") < len_tokens(without_content, "gpt-4")


def test_get_prompt_selective_components():
    """Test selective mode filters components correctly."""
    # Empty selective should be minimal
    empty_selective = get_prompt(
        get_tools(),
        prompt="full",
        context_mode="selective",
        context_include=[],
    )

    # Should have at least one message (core prompt)
    assert len(empty_selective) >= 1

    # Full mode should have more content
    full_mode = get_prompt(get_tools(), prompt="full", context_mode="full")
    assert len(full_mode) >= len(empty_selective)


def test_prompt_systeminfo_uses_workspace(tmp_path):
    """Test that prompt_systeminfo uses the provided workspace path."""
    from gptme.prompts import prompt_systeminfo

    msgs = list(prompt_systeminfo(workspace=tmp_path))
    content = "\n".join(msg.content for msg in msgs)
    assert str(tmp_path) in content, (
        f"Expected {tmp_path} in system prompt, got: {content[:200]}"
    )


def test_glob_path_traversal_protection(tmp_path):
    """Test that glob patterns cannot traverse outside the workspace.

    Issue #1036 Finding #2: Glob patterns like '../../etc/passwd' should be
    rejected to prevent path traversal attacks via gptme.toml configuration.
    """
    from gptme.prompts import prompt_workspace

    # Create a temp workspace with a file
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Test")

    # Create a file outside workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret data")

    # Create a gptme.toml with path traversal attempt
    (workspace / "gptme.toml").write_text(
        """
[prompt]
files = ["../outside/secret.txt", "README.md"]
"""
    )

    # Get workspace content
    msgs = list(prompt_workspace(workspace, include_user_context=False))
    content = "\n".join(msg.content for msg in msgs)

    # Collect attached files from all messages
    attached_files: list[str] = []
    for msg in msgs:
        attached_files.extend(str(f) for f in msg.files)

    # README should be attached, secret file should be blocked (path traversal)
    assert any("README.md" in f for f in attached_files), "README.md should be attached"
    assert not any("secret.txt" in f for f in attached_files), (
        "secret.txt should NOT be attached (path traversal)"
    )
    assert "../outside/secret.txt" not in content, "Path traversal should be blocked"


def test_workspace_git_status_in_git_repo(tmp_path):
    """Test that git status is included in workspace prompt for git repos."""
    import subprocess

    from gptme.prompts.workspace import _get_git_status

    workspace = tmp_path / "repo"
    workspace.mkdir()

    # Initialize a git repo on a test branch (avoids master-protection hooks)
    subprocess.run(
        ["git", "init", "-b", "test-branch"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )

    # Clean repo (no commits yet, but git status should still work)
    result = _get_git_status(workspace)
    assert result is not None
    assert "Branch" in result

    # Add a file and check status shows it
    (workspace / "hello.txt").write_text("hello")
    result = _get_git_status(workspace)
    assert result is not None
    assert "hello.txt" in result
    assert "Branch" in result

    # Commit and verify clean status
    subprocess.run(
        ["git", "add", "hello.txt"], cwd=workspace, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    result = _get_git_status(workspace)
    assert result is not None
    assert "(clean)" in result
    assert "test-branch" in result


def test_dynamic_context_after_static(tmp_path):
    """Test that context_cmd output comes after static workspace content.

    This ordering improves prompt caching: static/semi-static content first
    (cacheable prefix), dynamic context last (changes every session).
    """
    from gptme.prompts import get_prompt

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Test Project")
    (workspace / "gptme.toml").write_text(
        '[prompt]\nfiles = ["README.md"]\ncontext_cmd = "echo DYNAMIC_MARKER_123"\n'
    )

    msgs = get_prompt(
        get_tools(),
        prompt="full",
        workspace=workspace,
    )

    # Find the indices of workspace file content and dynamic context
    file_idx = None
    dynamic_idx = None
    for i, msg in enumerate(msgs):
        if "README.md" in msg.content:
            file_idx = i
        if "DYNAMIC_MARKER_123" in msg.content:
            dynamic_idx = i

    assert file_idx is not None, "Should include workspace files"
    assert dynamic_idx is not None, "Should include context_cmd output"
    assert dynamic_idx > file_idx, (
        f"Dynamic context (msg {dynamic_idx}) should come after "
        f"static workspace files (msg {file_idx})"
    )


def test_dynamic_context_has_explicit_cache_boundary(tmp_path):
    """Dynamic context should be separated by a stable cache-boundary marker."""
    from gptme.prompts import SYSTEM_PROMPT_CACHE_BOUNDARY, get_prompt

    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Test Project")
    (workspace / "gptme.toml").write_text(
        '[prompt]\nfiles = ["README.md"]\ncontext_cmd = "echo DYNAMIC_MARKER_456"\n'
    )

    msgs = get_prompt(
        get_tools(),
        prompt="full",
        workspace=workspace,
    )

    file_idx = None
    boundary_idx = None
    dynamic_idx = None
    for i, msg in enumerate(msgs):
        if "README.md" in msg.content:
            file_idx = i
        if SYSTEM_PROMPT_CACHE_BOUNDARY in msg.content:
            boundary_idx = i
        if "DYNAMIC_MARKER_456" in msg.content:
            dynamic_idx = i

    assert file_idx is not None, "Should include workspace files"
    assert boundary_idx is not None, "Should include cache-boundary marker"
    assert dynamic_idx is not None, "Should include context_cmd output"
    assert file_idx < boundary_idx < dynamic_idx


def test_prompt_workspace_sorts_glob_matches_deterministically(tmp_path, monkeypatch):
    """Glob matches should not depend on filesystem traversal order."""
    from gptme.prompts import prompt_workspace

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "z-last.md").write_text("z")
    (workspace / "README.md").write_text("# Readme")
    (workspace / "a-first.md").write_text("a")
    (workspace / "gptme.toml").write_text('[prompt]\nfiles = ["*.md"]\n')

    real_glob = Path.glob

    def fake_glob(self, pattern):
        if self == workspace and pattern == "*.md":
            return iter(
                [
                    workspace / "z-last.md",
                    workspace / "a-first.md",
                    workspace / "README.md",
                ]
            )
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", fake_glob)

    msgs = list(prompt_workspace(workspace, include_user_context=False))
    selected_files = [str(f) for msg in msgs for f in msg.files]
    selected_names = [Path(f).name for f in selected_files]

    assert selected_names == ["README.md", "a-first.md", "z-last.md"]


def test_workspace_git_status_not_git_repo(tmp_path):
    """Test that git status returns None for non-git directories."""
    from gptme.prompts.workspace import _get_git_status

    result = _get_git_status(tmp_path)
    assert result is None


def test_workspace_git_status_in_prompt(tmp_path):
    """Test that git status section appears in workspace prompt messages."""
    import subprocess

    from gptme.prompts import prompt_workspace

    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "test-branch"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )

    # Add an untracked file so status shows something
    (workspace / "README.md").write_text("# Test")

    msgs = list(prompt_workspace(workspace))
    content = "\n".join(msg.content for msg in msgs)
    assert "Git Status" in content
    assert "Branch" in content


def test_prompt_workspace_can_skip_user_context(tmp_path, monkeypatch):
    """User-level prompt files and AGENTS.md should be optional."""
    from gptme.prompts import prompt_workspace

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Local README")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    user_agent = config_dir / "AGENTS.md"
    user_agent.write_text("# User Agent Rules")
    user_notes = tmp_path / "user-notes.md"
    user_notes.write_text("absolute path leak")

    cfg = SimpleNamespace(
        user=SimpleNamespace(
            prompt=SimpleNamespace(files=[str(user_notes)]),
        )
    )
    monkeypatch.setattr(
        "gptme.prompts.workspace.config_path",
        str(config_dir / "config.toml"),
    )
    monkeypatch.setattr("gptme.prompts.workspace.get_config", lambda: cfg)

    msgs_with_user = list(prompt_workspace(workspace, include_user_context=True))
    msgs_without_user = list(prompt_workspace(workspace, include_user_context=False))

    files_with_user = [str(f) for msg in msgs_with_user for f in msg.files]
    files_without_user = [str(f) for msg in msgs_without_user for f in msg.files]

    assert str((workspace / "README.md").resolve()) in files_with_user
    assert str((workspace / "README.md").resolve()) in files_without_user
    assert str(user_agent.resolve()) in files_with_user
    assert str(user_notes.resolve()) in files_with_user
    assert str(user_agent.resolve()) not in files_without_user
    assert str(user_notes.resolve()) not in files_without_user


def test_prompt_workspace_skip_home_agents_md(tmp_path, monkeypatch):
    """~/AGENTS.md must not leak when workspace is under the home directory.

    This covers the gap in the original test: eval workspaces default to
    ~/.local/share/gptme/logs/..., so find_agent_files_in_tree walks through
    the home dir and picks up ~/AGENTS.md unless the tree-walk is gated by
    include_user_context.
    """
    from gptme.prompts import prompt_workspace

    # Simulate a home dir and an eval workspace inside it
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    eval_workspace = fake_home / ".local" / "share" / "gptme" / "logs" / "eval-task"
    eval_workspace.mkdir(parents=True)

    # Place an AGENTS.md in the fake home (the file that must not leak)
    home_agents = fake_home / "AGENTS.md"
    home_agents.write_text("# Home user agent rules — must not appear in evals")

    # Also place a project-level AGENTS.md in the workspace itself
    project_agents = eval_workspace / "AGENTS.md"
    project_agents.write_text("# Project-level agent rules")

    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    # Minimal config with no user files
    cfg = SimpleNamespace(user=SimpleNamespace(prompt=SimpleNamespace(files=[])))
    monkeypatch.setattr("gptme.prompts.workspace.get_config", lambda: cfg)
    monkeypatch.setattr(
        "gptme.prompts.workspace.config_path",
        str(fake_home / ".config" / "gptme" / "config.toml"),
    )

    msgs_without_user = list(
        prompt_workspace(eval_workspace, include_user_context=False)
    )
    files_without_user = [str(f) for msg in msgs_without_user for f in msg.files]

    assert str(home_agents.resolve()) not in files_without_user, (
        "~/AGENTS.md must not appear in eval runs (include_user_context=False)"
    )
