def test_get_tree_output_different_methods(tmp_path, monkeypatch):
    """Test that get_tree_output works with different tree methods."""
    from typing import cast

    from gptme.util.tree import TreeMethod, get_tree_output

    # Enable GPTME_CONTEXT_TREE
    monkeypatch.setenv("GPTME_CONTEXT_TREE", "true")

    # Mock subprocess to track which command is called
    import subprocess

    called_commands = []

    def mock_run(*args, **kwargs):
        called_commands.append(args[0])
        if args[0][:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(
                args[0], returncode=0, stdout="true", stderr=""
            )
        return subprocess.CompletedProcess(
            args[0], returncode=0, stdout="output", stderr=""
        )

    monkeypatch.setattr("gptme.util.tree.subprocess.run", mock_run)

    # Test different methods
    for method_str in ["tree", "git", "ls"]:
        method = cast(TreeMethod, method_str)
        called_commands.clear()
        result = get_tree_output(tmp_path, method=method)
        assert result == "output"

        # Verify the correct command was called
        if method == "tree":
            assert any(
                "tree" in cmd for cmd in called_commands if isinstance(cmd, list)
            )
        elif method == "git":
            assert any(
                cmd[:2] == ["git", "ls-files"]
                for cmd in called_commands
                if isinstance(cmd, list)
            )
        elif method == "ls":
            assert any(
                cmd[:2] == ["ls", "-R"]
                for cmd in called_commands
                if isinstance(cmd, list)
            )


def test_get_tree_output_disabled(tmp_path, monkeypatch):
    """Test that get_tree_output returns None when GPTME_CONTEXT_TREE is disabled."""
    from gptme.util.tree import get_tree_output

    # Mock config to return False for GPTME_CONTEXT_TREE
    monkeypatch.setenv("GPTME_CONTEXT_TREE", "false")

    result = get_tree_output(tmp_path)
    assert result is None


def test_get_tree_output_fallback_when_tree_missing(tmp_path, monkeypatch):
    """Test that get_tree_output falls back to git when tree command fails."""
    from gptme.util.tree import get_tree_output

    # Enable GPTME_CONTEXT_TREE
    monkeypatch.setenv("GPTME_CONTEXT_TREE", "true")

    import subprocess

    called_commands = []

    def mock_run(*args, **kwargs):
        called_commands.append(args[0])
        if args[0][:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(
                args[0], returncode=0, stdout="true", stderr=""
            )
        if isinstance(args[0], list) and args[0][0] == "tree":
            # tree command fails (not installed)
            return subprocess.CompletedProcess(
                args[0], returncode=127, stdout="", stderr="tree: command not found"
            )
        if isinstance(args[0], list) and args[0][:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(
                args[0], returncode=0, stdout="file1.txt\nfile2.txt", stderr=""
            )
        return subprocess.CompletedProcess(args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("gptme.util.tree.subprocess.run", mock_run)

    # Request tree method, but it should fall back to git
    result = get_tree_output(tmp_path, method="tree")
    assert result == "file1.txt\nfile2.txt"

    # Verify git ls-files was called after tree failed
    assert any(isinstance(cmd, list) and cmd[0] == "tree" for cmd in called_commands)
    assert any(
        isinstance(cmd, list) and cmd[:2] == ["git", "ls-files"]
        for cmd in called_commands
    )


def test_get_tree_output_not_git_repo(tmp_path, monkeypatch):
    """Test that get_tree_output falls back to ls when not in a git repository."""
    from gptme.util.tree import get_tree_output

    # Enable GPTME_CONTEXT_TREE
    monkeypatch.setenv("GPTME_CONTEXT_TREE", "true")

    # Mock subprocess to simulate not being in a git repo
    import subprocess

    called_commands = []

    def mock_run(*args, **kwargs):
        called_commands.append(args[0])
        if args[0][:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(
                args[0], returncode=1, stdout="", stderr="not a git repository"
            )
        if isinstance(args[0], list) and args[0][:2] == ["ls", "-R"]:
            return subprocess.CompletedProcess(
                args[0], returncode=0, stdout="file1.txt\nfile2.txt", stderr=""
            )
        return subprocess.CompletedProcess(args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("gptme.util.tree.subprocess.run", mock_run)

    result = get_tree_output(tmp_path)
    # Should fall back to ls and return its output
    assert result == "file1.txt\nfile2.txt"
    # Verify ls was called (not git ls-files or tree)
    assert any(
        isinstance(cmd, list) and cmd[:2] == ["ls", "-R"] for cmd in called_commands
    )
    assert not any(
        isinstance(cmd, list) and cmd[:2] == ["git", "ls-files"]
        for cmd in called_commands
    )


def test_get_tree_output_command_fails(tmp_path, monkeypatch):
    """Test that get_tree_output returns None when all methods fail."""
    from gptme.util.tree import get_tree_output

    # Enable GPTME_CONTEXT_TREE
    monkeypatch.setenv("GPTME_CONTEXT_TREE", "true")

    # Mock subprocess to simulate git repo but all listing commands fail
    import subprocess

    def mock_run(*args, **kwargs):
        if args[0][:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(
                args[0], returncode=0, stdout="true", stderr=""
            )
        # all methods fail
        return subprocess.CompletedProcess(
            args[0], returncode=1, stdout="", stderr="command failed"
        )

    monkeypatch.setattr("gptme.util.tree.subprocess.run", mock_run)

    result = get_tree_output(tmp_path)
    assert result is None


def test_get_tree_output_too_long(tmp_path, monkeypatch):
    """Test that get_tree_output handles overly long output with depth reduction."""
    from gptme.util.tree import get_tree_output

    # Enable GPTME_CONTEXT_TREE
    monkeypatch.setenv("GPTME_CONTEXT_TREE", "true")

    # Mock subprocess to return very long output for all methods
    import subprocess

    def mock_run(*args, **kwargs):
        if args[0][:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(
                args[0], returncode=0, stdout="true", stderr=""
            )
        long_output = "file.txt\n" * 5000  # > 20000 characters
        return subprocess.CompletedProcess(
            args[0], returncode=0, stdout=long_output, stderr=""
        )

    monkeypatch.setattr("gptme.util.tree.subprocess.run", mock_run)

    # All lines are "file.txt" (no '/' in path, so depth 0).
    # 5000 lines × 9 chars = 45000 > 20000 budget, and depth reduction
    # cannot shrink depth-0-only output, so all methods return None.
    result = get_tree_output(tmp_path)
    assert result is None


def test_get_tree_output_success(tmp_path, monkeypatch):
    """Test that get_tree_output returns output when everything works."""
    from gptme.util.tree import get_tree_output

    # Enable GPTME_CONTEXT_TREE
    monkeypatch.setenv("GPTME_CONTEXT_TREE", "true")

    # Mock subprocess to return successful output
    import subprocess

    expected_output = "file1.txt\nfile2.txt\nsrc/main.py"

    def mock_run(*args, **kwargs):
        if args[0][:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(
                args[0], returncode=0, stdout="true", stderr=""
            )
        return subprocess.CompletedProcess(
            args[0], returncode=0, stdout=expected_output, stderr=""
        )

    monkeypatch.setattr("gptme.util.tree.subprocess.run", mock_run)

    result = get_tree_output(tmp_path)
    assert result == expected_output


def test_reduce_tree_output_by_depth():
    """Test _reduce_tree_output_by_depth function."""
    from gptme.util.tree import _reduce_tree_output_by_depth

    # Test with output that needs reduction
    output = "\n".join(  # noqa: FLY002
        [
            "file1.txt",
            "dir1/file2.txt",
            "dir1/subdir1/file3.txt",
            "dir1/subdir1/deep/file4.txt",
        ]
    )

    # Should reduce to depth 2 when budget allows it (47 chars < 50)
    result = _reduce_tree_output_by_depth(output, budget=50)
    lines = result.split("\n") if result else []
    assert all(line.count("/") <= 2 for line in lines)

    # Should reduce to depth 1 when budget is tighter
    result = _reduce_tree_output_by_depth(output, budget=30)
    lines = result.split("\n") if result else []
    assert all(line.count("/") <= 1 for line in lines)

    # Should return None if even depth 0 exceeds budget
    very_long_output = "very_long_filename_that_exceeds_budget.txt" * 1000
    result = _reduce_tree_output_by_depth(very_long_output, budget=10)
    assert result is None

    # Should return original output if within budget
    short_output = "file1.txt\nfile2.txt"
    result = _reduce_tree_output_by_depth(short_output, budget=1000)
    assert result == short_output

    # Should handle empty output
    result = _reduce_tree_output_by_depth("", budget=100)
    assert result == ""
