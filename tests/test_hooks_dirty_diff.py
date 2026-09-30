"""Tests for the dirty-working-tree diff injection hook (gptme/gptme#4009 follow-on)."""

import subprocess
from pathlib import Path

import pytest

from gptme.hooks import StopPropagation
from gptme.message import Message


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "test-branch", cwd=repo)
    _git("config", "user.email", "bob@superuserlabs.org", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("original\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    return repo


class _FakeManager:
    def __init__(self, messages, workspace):
        self.log = messages
        self.logdir = None
        self.workspace = workspace


def _run(initial_msgs, workspace: Path | None, manager=None):
    from gptme.hooks.dirty_diff import inject_dirty_diff

    return [
        item
        for item in inject_dirty_diff(
            logdir=None,
            workspace=workspace,
            initial_msgs=initial_msgs,
            manager=manager,
        )
        if not isinstance(item, StopPropagation)
    ]


def test_hook_yields_hidden_system_message_for_dirty_tree(git_repo):
    from gptme.hooks.dirty_diff import _INJECT_SENTINEL

    (git_repo / "file.txt").write_text("changed\n")

    out = _run([], git_repo)

    assert len(out) == 1
    msg = out[0]
    assert msg.role == "system"
    assert msg.hide is True
    assert msg.content.lstrip().startswith(_INJECT_SENTINEL)
    assert "```diff" in msg.content
    assert "-original" in msg.content
    assert "+changed" in msg.content


def test_hook_yields_nothing_for_clean_tree(git_repo):
    assert _run([], git_repo) == []


def test_hook_yields_nothing_without_workspace():
    assert _run([], None) == []


def test_hook_yields_nothing_for_non_git_directory(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _run([], plain) == []


def test_hook_skips_when_already_injected(git_repo):
    from gptme.hooks.dirty_diff import _INJECT_SENTINEL

    (git_repo / "file.txt").write_text("changed\n")
    prior = Message(
        "system",
        f"{_INJECT_SENTINEL}\nalready injected\n",
        hide=True,
    )
    manager = _FakeManager([prior], git_repo)
    assert _run(None, git_repo, manager=manager) == []


def test_hook_reads_workspace_from_manager(git_repo):
    (git_repo / "file.txt").write_text("changed\n")
    manager = _FakeManager([], git_repo)
    out = _run([], None, manager=manager)
    assert len(out) == 1
    assert "+changed" in out[0].content


def test_hook_truncates_large_diffs(git_repo):
    from gptme.hooks.dirty_diff import _MAX_DIFF_CHARS

    (git_repo / "file.txt").write_text("x" * (_MAX_DIFF_CHARS * 2) + "\n")

    out = _run([], git_repo)

    assert len(out) == 1
    assert "(truncated)" in out[0].content
    assert len(out[0].content) < _MAX_DIFF_CHARS * 2


def test_hook_swallows_unexpected_errors(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("gptme.hooks.dirty_diff._get_dirty_diff", boom)
    (git_repo / "file.txt").write_text("changed\n")

    assert _run([], git_repo) == []


def test_register_adds_session_start_and_turn_pre_hooks():
    from gptme.hooks import HookType, clear_hooks, get_hooks
    from gptme.hooks.dirty_diff import register

    clear_hooks()
    register()
    start_names = [hook.name for hook in get_hooks(HookType.SESSION_START)]
    turn_names = [hook.name for hook in get_hooks(HookType.TURN_PRE)]
    assert "dirty_diff.session_start" in start_names
    assert "dirty_diff.turn_pre" in turn_names


def test_hook_redacts_secret_assignments(git_repo):
    (git_repo / "file.txt").write_text("GITHUB_TOKEN=ghp_supersecretvalue\n")

    out = _run([], git_repo)

    assert len(out) == 1
    assert "GITHUB_TOKEN=[REDACTED]" in out[0].content
    assert "ghp_supersecretvalue" not in out[0].content


def test_hook_lists_untracked_files(git_repo):
    (git_repo / "brand_new.py").write_text("print('hi')\n")

    out = _run([], git_repo)

    assert len(out) == 1
    assert "brand_new.py" in out[0].content
    assert "Untracked files" in out[0].content
    # Untracked contents are not diffed, so no diff fence is emitted.
    assert "```diff" not in out[0].content


def test_hook_marks_content_untrusted(git_repo):
    (git_repo / "file.txt").write_text("changed\n")

    out = _run([], git_repo)

    assert len(out) == 1
    assert "untrusted repository data" in out[0].content


def test_hook_redacts_secrets_in_untracked_filenames(git_repo):
    (git_repo / "GITHUB_TOKEN=ghp_supersecretvalue").write_text("x\n")

    out = _run([], git_repo)

    assert len(out) == 1
    assert "GITHUB_TOKEN=[REDACTED]" in out[0].content
    assert "ghp_supersecretvalue" not in out[0].content


def test_hook_bounds_untracked_enumeration(git_repo):
    from gptme.hooks.dirty_diff import _MAX_UNTRACKED_PATHS

    for i in range(_MAX_UNTRACKED_PATHS + 5):
        (git_repo / f"new_{i:03d}.py").write_text("x\n")

    out = _run([], git_repo)

    assert len(out) == 1
    content = out[0].content
    assert "new_000.py" in content
    # Only the cap is listed; the overflow is summarized rather than counted.
    assert f"new_{_MAX_UNTRACKED_PATHS - 1:03d}.py" in content
    assert f"new_{_MAX_UNTRACKED_PATHS:03d}.py" not in content
    assert "list truncated" in content


def test_git_capture_bounded_times_out(git_repo, monkeypatch):
    import threading

    from gptme.hooks import dirty_diff

    class _BlockingStdout:
        def __init__(self, stop: threading.Event):
            self._stop = stop

        def __iter__(self):
            # Block like a hung git until the fake process is killed.
            self._stop.wait()
            return
            yield  # pragma: no cover

        def close(self):
            pass

    class _FakeProc:
        def __init__(self):
            self._stop = threading.Event()
            self.stdout = _BlockingStdout(self._stop)
            self.returncode: int | None = None

        def poll(self):
            return None

        def kill(self):
            self.returncode = -9
            self._stop.set()

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(dirty_diff.subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(dirty_diff, "_DIFF_TIMEOUT_SECONDS", 0.05)

    assert dirty_diff._git_capture_bounded(git_repo, "ls-files", max_lines=50) is None


def test_redact_diff_handles_content_starting_with_marker():
    from gptme.hooks.dirty_diff import _redact_diff

    # An added line whose own content begins with `+++` renders as `++++...`.
    assert (
        _redact_diff("++++GITHUB_TOKEN=ghp_leak\n") == "++++GITHUB_TOKEN=[REDACTED]\n"
    )
    # A deleted line whose content begins with `---`.
    assert _redact_diff("----PASSWORD=hunter2\n") == "----PASSWORD=[REDACTED]\n"
    # Header paths are redacted after their `a/`/`b/` prefix.
    assert (
        _redact_diff("+++ b/GITHUB_TOKEN=ghp_leak\n")
        == "+++ b/GITHUB_TOKEN=[REDACTED]\n"
    )
    # ... including the `diff --git` line, which has no +/- marker.
    assert (
        _redact_diff("diff --git a/GITHUB_TOKEN=ghp_leak b/GITHUB_TOKEN=ghp_leak\n")
        == "diff --git a/GITHUB_TOKEN=[REDACTED] b/GITHUB_TOKEN=[REDACTED]\n"
    )


def test_get_dirty_diff_disables_ext_and_textconv(git_repo, monkeypatch):
    import io

    from gptme.hooks import dirty_diff

    captured: dict = {}

    class _FakeProc:
        def __init__(self):
            self.stdout = io.StringIO("diff --git a/f b/f\n")
            self.returncode = 0

        def poll(self):
            return 0

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(dirty_diff.subprocess, "Popen", fake_popen)

    result = dirty_diff._get_dirty_diff(git_repo)

    assert result is not None
    assert "--no-ext-diff" in captured["argv"]
    assert "--no-textconv" in captured["argv"]


def test_redact_diff_keeps_legitimate_assignment_filenames():
    from gptme.hooks.dirty_diff import _redact_diff

    # A filename that merely looks like an assignment is not a secret: the
    # value (`parser.py`) has no credential shape, so the path stays readable.
    assert _redact_diff("+++ b/token=parser.py\n") == "+++ b/token=parser.py\n"
    assert (
        _redact_diff("diff --git a/token=parser.py b/token=parser.py\n")
        == "diff --git a/token=parser.py b/token=parser.py\n"
    )
    # A secret-shaped value in the same position is still redacted.
    assert (
        _redact_diff("diff --git a/GITHUB_TOKEN=ghp_leak b/GITHUB_TOKEN=ghp_leak\n")
        == "diff --git a/GITHUB_TOKEN=[REDACTED] b/GITHUB_TOKEN=[REDACTED]\n"
    )


def test_hook_lists_legit_assignment_filename_untracked(git_repo):
    (git_repo / "token=parser.py").write_text("print('hi')\n")

    out = _run([], git_repo)

    assert len(out) == 1
    assert "token=parser.py" in out[0].content
    assert "token=[REDACTED]" not in out[0].content


def test_redact_secret_values_in_path_unit():
    from gptme.util.redact import redact_secret_values_in_path

    assert redact_secret_values_in_path("token=parser.py") == "token=parser.py"
    assert redact_secret_values_in_path("config/token=parser.py") == (
        "config/token=parser.py"
    )
    assert redact_secret_values_in_path("GITHUB_TOKEN=ghp_abc123") == (
        "GITHUB_TOKEN=[REDACTED]"
    )
    assert redact_secret_values_in_path("api_key=sk-proj-abcdef123456") == (
        "api_key=[REDACTED]"
    )
    # Unprefixed but clearly opaque: long mixed-case or long hex runs redact.
    assert redact_secret_values_in_path("token=AbCdEf0123456789AbCdEf01") == (
        "token=[REDACTED]"
    )
    assert redact_secret_values_in_path("token=abcdef0123456789abcdef01") == (
        "token=[REDACTED]"
    )
    # Not an assignment-shaped path → untouched.
    assert redact_secret_values_in_path("src/main.py") == "src/main.py"
    # Assignment-shaped but a plausible filename → preserved.
    assert redact_secret_values_in_path("token=config.json") == "token=config.json"
