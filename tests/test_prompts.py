import os

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
    msgs = list(prompt_workspace(workspace))
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
