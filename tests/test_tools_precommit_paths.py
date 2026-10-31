"""Real Git/runner regressions for lossless modified-file selection (#4681).

The hook records its actual argv outside the repository before rejecting bad
content. Checking that record (including absence of clean sentinel files) makes
both a skipped hook and an accidental --all-files fallback observable. No remote
hooks, virtualenv installation, or provider calls are needed.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from gptme.hooks import HookType, StopPropagation
from gptme.logmanager import Log, LogManager
from gptme.message import Message
from gptme.tools import precommit

POSIX_PATH = pytest.mark.skipif(
    os.name == "nt", reason="Windows disallows LF/trailing whitespace in paths"
)

PATH_NAMES = [
    pytest.param("café.txt", id="unicode"),
    pytest.param("line\nbreak.txt", id="newline", marks=POSIX_PATH),
    pytest.param(" leading.txt", id="leading-space"),
    pytest.param("trailing.txt ", id="trailing-space", marks=POSIX_PATH),
    pytest.param("two words.txt", id="embedded-space"),
    pytest.param("--verbose", id="option-like"),
]


@dataclass
class HookRepo:
    root: Path
    record: Path

    def git(self, *args: str, input: str | None = None) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            input=input,
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout.strip()

    def write(self, name: str, content: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def checkpoint(self) -> None:
        """Seed disposable history without invoking unrelated installed hooks."""
        self.git("add", "--all")
        tree = self.git("write-tree")
        commit = self.git("commit-tree", tree, input="Test baseline\n")
        self.git("update-ref", "HEAD", commit)

    def recorded_files(self) -> list[str]:
        assert self.record.is_file(), "The real local hook was never executed"
        result = json.loads(self.record.read_text(encoding="utf-8"))
        assert isinstance(result, list)
        assert all(isinstance(name, str) for name in result)
        return result

    def assert_rejected(self, expected: list[str]) -> None:
        success, output = precommit.run_precommit_checks(all_files=False)
        assert success is False, "Changed targets were falsely reported as passing"
        assert output is not None and "REJECTED TARGET" in output, output
        actual = self.recorded_files()
        # Runner argv may safely prefix option-like paths; preserve every other
        # byte of ordinary filenames rather than broadly normalizing paths.
        actual = [
            name.removeprefix("./") if name.startswith("./-") else name
            for name in actual
        ]
        assert sorted(actual) == sorted(expected), (actual, expected)
        assert len(actual) == len(set(actual)), "Hook received duplicate filenames"


@pytest.fixture
def hook_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HookRepo:
    # Both runners support embedded root newlines/spaces. prek 0.3.3 itself
    # strips trailing root whitespace, so test that case in discovery only.
    root_name = (
        "repository with spaces"
        if os.name == "nt"
        else " repository with spaces\nand newline"
    )
    root = tmp_path / root_name
    root.mkdir()
    record = tmp_path / "hook-argv.json"
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Path Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "paths@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Path Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "paths@example.invalid")
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "precommit-cache"))
    monkeypatch.setenv("PREK_HOME", str(tmp_path / "prek-cache"))
    monkeypatch.setenv("GPTME_TEST_HOOK_RECORD", str(record))
    monkeypatch.delenv("SKIP", raising=False)
    repo = HookRepo(root, record)
    repo.git("init", "--quiet")
    repo.git("config", "core.quotePath", "true")
    repo.write(
        "record_hook.py",
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "pathlib.Path(os.environ['GPTME_TEST_HOOK_RECORD']).write_text(\n"
        "    json.dumps(args), encoding='utf-8')\n"
        "if any(pathlib.Path(name).read_bytes() == b'REJECT\\n' for name in args):\n"
        "    print('REJECTED TARGET')\n"
        "    sys.exit(1)\n",
    )
    entry = json.dumps(shlex.join([sys.executable, "record_hook.py"]))
    repo.write(
        ".pre-commit-config.yaml",
        "repos:\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: record-and-reject\n"
        "        name: Record exact paths and reject changed targets\n"
        "        language: system\n"
        f"        entry: {entry}\n"
        "        files: '\\.txt$|\\.txt $|^(?:\\./)?--verbose$'\n"
        "        require_serial: true\n",
    )
    repo.write("clean-sentinel.txt", "clean\n")
    repo.checkpoint()
    monkeypatch.chdir(root)
    return repo


@pytest.fixture(params=["pre-commit", "prek"])
def runner(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    name = str(request.param)
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} is not installed")
    # Only enablement and runner selection are mocked; Git and hooks are real.
    monkeypatch.setattr(precommit, "use_checks", lambda: True)
    monkeypatch.setattr(precommit, "get_precommit_runner", lambda: executable)
    return name


@pytest.mark.parametrize("name", PATH_NAMES)
@pytest.mark.parametrize("state", ["staged-only", "unstaged", "untracked"])
def test_changed_path_reaches_real_hook(
    hook_repo: HookRepo, runner: str, name: str, state: str
) -> None:
    if state != "untracked":
        hook_repo.write(name, "baseline\n")
        hook_repo.checkpoint()
    hook_repo.write(name, "REJECT\n")
    if state == "staged-only":
        hook_repo.git("add", "--", name)
    hook_repo.assert_rejected([name])


@pytest.mark.parametrize("name", PATH_NAMES)
def test_discovery_preserves_path_verbatim(hook_repo: HookRepo, name: str) -> None:
    hook_repo.write(name, "new\n")
    assert precommit._get_modified_files() == [name]


def test_staged_and_unstaged_overlap_is_checked_once(
    hook_repo: HookRepo, runner: str
) -> None:
    name = "overlap.txt"
    hook_repo.write(name, "baseline\n")
    hook_repo.checkpoint()
    hook_repo.write(name, "staged change\n")
    hook_repo.git("add", "--", name)
    hook_repo.write(name, "REJECT\n")
    hook_repo.write("staged-only.txt", "REJECT\n")
    hook_repo.git("add", "--", "staged-only.txt")
    hook_repo.write("untracked.txt", "REJECT\n")
    expected = [name, "staged-only.txt", "untracked.txt"]
    assert precommit._get_modified_files() == sorted(expected)
    hook_repo.assert_rejected(expected)


def test_staged_then_reverted_worktree_still_reaches_hook(
    hook_repo: HookRepo, runner: str
) -> None:
    name = "reverted.txt"
    hook_repo.write(name, "REJECT\n")
    hook_repo.checkpoint()
    hook_repo.write(name, "staged change\n")
    hook_repo.git("add", "--", name)
    hook_repo.write(name, "REJECT\n")
    # Worktree equals HEAD but differs from the staged version: diff HEAD alone
    # incorrectly finds no change and falls back to checking the clean sentinel.
    assert precommit._get_modified_files() == [name]
    hook_repo.assert_rejected([name])


def test_unborn_repository_checks_staged_and_untracked(
    hook_repo: HookRepo, runner: str
) -> None:
    # Keep the index but remove the baseline ref to model a first commit.
    hook_repo.git("update-ref", "-d", "HEAD")
    hook_repo.write("staged-first.txt", "REJECT\n")
    hook_repo.git("add", "--", "staged-first.txt")
    hook_repo.write("untracked-first.txt", "REJECT\n")
    discovered = precommit._get_modified_files()
    assert "staged-first.txt" in discovered
    assert "untracked-first.txt" in discovered
    # The initial index also contains clean-sentinel.txt; all are legitimate
    # first-commit changes, unlike the clean sentinel in the other scenarios.
    hook_repo.assert_rejected(
        ["clean-sentinel.txt", "staged-first.txt", "untracked-first.txt"]
    )


def test_nested_cwd_includes_changes_outside_subtree(
    hook_repo: HookRepo, runner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_repo.write("nested/inside.txt", "baseline\n")
    hook_repo.write("outside.txt", "baseline\n")
    hook_repo.checkpoint()
    hook_repo.write("nested/inside.txt", "REJECT\n")
    hook_repo.write("outside.txt", "REJECT\n")
    hook_repo.write("untracked-outside.txt", "REJECT\n")
    monkeypatch.chdir(hook_repo.root / "nested")
    hook_repo.assert_rejected(
        ["nested/inside.txt", "outside.txt", "untracked-outside.txt"]
    )


@pytest.mark.parametrize("staged_deletion", [False, True])
def test_deletion_does_not_hide_surviving_changes(
    hook_repo: HookRepo, runner: str, staged_deletion: bool
) -> None:
    hook_repo.write("deleted.txt", "baseline\n")
    hook_repo.write("survivor.txt", "baseline\n")
    hook_repo.checkpoint()
    (hook_repo.root / "deleted.txt").unlink()
    if staged_deletion:
        hook_repo.git("add", "--", "deleted.txt")
    hook_repo.write("survivor.txt", "REJECT\n")
    hook_repo.write("new survivor.txt", "REJECT\n")
    hook_repo.assert_rejected(["survivor.txt", "new survivor.txt"])


def test_no_changes_falls_back_to_all_files(hook_repo: HookRepo, runner: str) -> None:
    # A rejecting but committed target makes the fallback observable, not merely
    # a successful invocation that could actually have skipped all hooks.
    hook_repo.write("committed-bad.txt", "REJECT\n")
    hook_repo.checkpoint()
    assert precommit._get_modified_files() == []
    hook_repo.assert_rejected(["clean-sentinel.txt", "committed-bad.txt"])


@POSIX_PATH
def test_discovery_from_root_with_trailing_whitespace(
    hook_repo: HookRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = hook_repo.root.with_name(hook_repo.root.name + " \n")
    hook_repo.root.rename(root)
    hook_repo.root = root
    monkeypatch.chdir(root)
    hook_repo.write("outside.txt", "REJECT\n")
    (root / "nested").mkdir()
    monkeypatch.chdir(root / "nested")
    assert precommit._get_modified_files() == ["outside.txt"]


def test_turn_post_rejection_blocks_autocommit(
    hook_repo: HookRepo, runner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "café changed.txt"
    hook_repo.write(name, "REJECT\n")
    manager = LogManager(logdir=hook_repo.root.parent / "conversation", lock=False)
    checked_logs: list[Log] = []

    def has_modifications(log: Log) -> bool:
        checked_logs.append(log)
        return True

    monkeypatch.setattr(precommit, "check_for_modifications", has_modifications)
    assert precommit.tool.hooks is not None
    hook_type, callback, _ = precommit.tool.hooks["precommit_full"]
    assert hook_type == HookType.TURN_POST.value
    assert callback is precommit.run_full_precommit_checks
    results = list(precommit.run_full_precommit_checks(manager))

    assert len(checked_logs) == 1 and checked_logs[0] is manager.log
    assert len(results) == 2
    message, stop = results
    assert isinstance(message, Message)
    assert "REJECTED TARGET" in message.content
    assert isinstance(stop, StopPropagation)
    assert hook_repo.recorded_files() == [name]


def test_discovery_retains_dangling_symlink_but_excludes_deleted_file(
    hook_repo: HookRepo,
) -> None:
    link = hook_repo.root / "dangling.txt"
    try:
        link.symlink_to("absent-target.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported: {exc}")
    assert link.is_symlink() and not link.exists()
    hook_repo.write("deleted.txt", "baseline\n")
    hook_repo.checkpoint()
    (hook_repo.root / "deleted.txt").unlink()
    # Make the tracked symlink itself a changed path, without creating a target.
    link.unlink()
    link.symlink_to("another-absent-target.txt")
    assert precommit._get_modified_files() == ["dangling.txt"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem byte names")
def test_discovery_preserves_undecodable_filename_bytes(hook_repo: HookRepo) -> None:
    # External runners have their own byte-filename limitations; only discovery
    # is required to preserve Python's filesystem surrogateescape round trip.
    raw_name = b"undecodable-\xff.txt"
    name = os.fsdecode(raw_name)
    if os.fsencode(name) != raw_name:
        pytest.skip("Filesystem encoding cannot round-trip this byte name")
    (hook_repo.root / name).write_bytes(b"new\n")
    assert precommit._get_modified_files() == [name]
