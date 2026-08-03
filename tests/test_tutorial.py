"""Unit tests for the gptme-tutorial command validators."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_tutorial import (
    BUGGY_SCRIPT,
    README,
    TASKS,
    TaskResult,
    _run_task,
    _validate_fix_bug,
    _validate_summarize,
    _validate_write_test,
    main,
)


@pytest.fixture()
def tutorial_dir(tmp_path: Path) -> Path:
    """Temp dir with the standard tutorial files."""
    (tmp_path / "README.md").write_text(README)
    (tmp_path / "buggy.py").write_text(BUGGY_SCRIPT)
    return tmp_path


# --- _validate_summarize ---


def test_validate_summarize_pass(tmp_path: Path) -> None:
    (tmp_path / "summary.md").write_text("This is a summary.\n")
    passed, msg = _validate_summarize(tmp_path)
    assert passed, msg


def test_validate_summarize_fail_no_summary(tmp_path: Path) -> None:
    passed, _ = _validate_summarize(tmp_path)
    assert not passed


def test_validate_summarize_fail_empty_summary(tmp_path: Path) -> None:
    (tmp_path / "summary.md").write_text("")
    passed, _ = _validate_summarize(tmp_path)
    assert not passed


# --- _validate_write_test ---


def test_validate_write_test_pass(tmp_path: Path) -> None:
    (tmp_path / "test_add.py").write_text("def test_add():\n    assert 1 + 1 == 2\n")
    passed, msg = _validate_write_test(tmp_path)
    assert passed, msg


def test_validate_write_test_pass_underscore_suffix(tmp_path: Path) -> None:
    (tmp_path / "add_test.py").write_text("def test_add():\n    assert 2 + 2 == 4\n")
    passed, msg = _validate_write_test(tmp_path)
    assert passed, msg


def test_validate_write_test_fail_no_file(tmp_path: Path) -> None:
    passed, _ = _validate_write_test(tmp_path)
    assert not passed


def test_validate_write_test_fail_no_test_fn(tmp_path: Path) -> None:
    (tmp_path / "test_add.py").write_text("# placeholder\n")
    passed, _ = _validate_write_test(tmp_path)
    assert not passed


def test_validate_write_test_fail_no_assert(tmp_path: Path) -> None:
    (tmp_path / "test_add.py").write_text("def test_add():\n    pass\n")
    passed, _ = _validate_write_test(tmp_path)
    assert not passed


# --- _validate_fix_bug ---


def test_validate_fix_bug_fail_original(tutorial_dir: Path) -> None:
    """The original BUGGY_SCRIPT has an off-by-one and should fail validation."""
    passed, _ = _validate_fix_bug(tutorial_dir)
    assert not passed


def test_validate_fix_bug_pass(tmp_path: Path) -> None:
    fixed = (
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n\n"
        "names = ['Alice', 'Bob', 'Charlie']\n"
        "for name in names:\n"
        "    print(greet(name))\n"
    )
    (tmp_path / "buggy.py").write_text(fixed)
    passed, msg = _validate_fix_bug(tmp_path)
    assert passed, msg


def test_validate_fix_bug_fail_no_file(tmp_path: Path) -> None:
    passed, _ = _validate_fix_bug(tmp_path)
    assert not passed


def test_validate_fix_bug_fail_runtime_error(tmp_path: Path) -> None:
    (tmp_path / "buggy.py").write_text("raise ValueError('still broken')\n")
    passed, _ = _validate_fix_bug(tmp_path)
    assert not passed


# --- _run_task ---


def test_run_task_retries_after_gptme_failure(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def fake_run(args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subprocess.CompletedProcess(args, 1)  # first attempt fails
        (tutorial_dir / "summary.md").write_text("This is a summary.\n")
        return subprocess.CompletedProcess(args, 0)  # second attempt succeeds

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("click.getchar", Mock(side_effect=["\n", "\n"]))

    result = _run_task(TASKS[0], tutorial_dir, 1, len(TASKS))

    assert result is TaskResult.COMPLETED
    assert call_count == 2


def test_run_task_does_not_validate_stale_summary_after_retry(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def fake_run(args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            (tutorial_dir / "summary.md").write_text("stale partial output\n")
            return subprocess.CompletedProcess(args, 1)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("click.getchar", Mock(side_effect=["\n", "\n", "s"]))

    result = _run_task(TASKS[0], tutorial_dir, 1, len(TASKS))

    assert result is TaskResult.SKIPPED
    assert call_count == 2
    assert not (tutorial_dir / "summary.md").exists()


def test_run_task_does_not_validate_stale_test_after_retry(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def fake_run(args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            (tutorial_dir / "stale_test.py").write_text(
                "def test_add():\n    assert 1 + 1 == 2\n"
            )
            return subprocess.CompletedProcess(args, 1)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("click.getchar", Mock(side_effect=["\n", "\n", "s"]))

    result = _run_task(TASKS[1], tutorial_dir, 2, len(TASKS))

    assert result is TaskResult.SKIPPED
    assert call_count == 2
    assert not (tutorial_dir / "stale_test.py").exists()


def test_run_task_ignores_matching_directories_during_retry_cleanup(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matching_dir = tutorial_dir / "stale_test.py"
    matching_dir.mkdir()

    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess(["gptme"], 0)),
    )
    monkeypatch.setattr("click.getchar", Mock(side_effect=["\n", "s"]))

    result = _run_task(TASKS[1], tutorial_dir, 2, len(TASKS))

    assert result is TaskResult.SKIPPED
    assert matching_dir.is_dir()


def test_run_task_replaces_matching_summary_directory(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matching_dir = tutorial_dir / "summary.md"
    matching_dir.mkdir()

    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess(["gptme"], 0)),
    )
    monkeypatch.setattr("click.getchar", Mock(side_effect=["\n", "s"]))

    result = _run_task(TASKS[0], tutorial_dir, 1, len(TASKS))

    assert result is TaskResult.SKIPPED
    assert not matching_dir.exists()


def test_run_task_replaces_matching_buggy_directory(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tutorial_dir / "buggy.py"
    script.unlink()
    script.mkdir()

    def fake_run(args, **kwargs):
        if args[0] == "gptme":
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(args, 1, stderr="still broken")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("click.getchar", Mock(side_effect=["\n", "s"]))

    result = _run_task(TASKS[2], tutorial_dir, 3, len(TASKS))

    assert result is TaskResult.SKIPPED
    assert script.is_file()
    assert script.read_text() == BUGGY_SCRIPT


def test_run_task_does_not_validate_stale_bug_fix_after_retry(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    def fake_run(args, **kwargs):
        nonlocal call_count
        if args[0] == "gptme":
            call_count += 1
            if call_count == 1:
                (tutorial_dir / "buggy.py").write_text("print('fixed')\n")
                return subprocess.CompletedProcess(args, 1)
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(args, 1, stderr="still broken")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("click.getchar", Mock(side_effect=["\n", "\n", "s"]))

    result = _run_task(TASKS[2], tutorial_dir, 3, len(TASKS))

    assert result is TaskResult.SKIPPED
    assert call_count == 2
    assert (tutorial_dir / "buggy.py").read_text() == BUGGY_SCRIPT


def test_run_task_reports_skip_separately(
    tutorial_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("click.getchar", Mock(return_value="s"))

    result = _run_task(TASKS[0], tutorial_dir, 1, len(TASKS))

    assert result is TaskResult.SKIPPED


def test_main_does_not_count_skipped_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("click.getchar", Mock(return_value="s"))

    result = CliRunner().invoke(main, ["--task", "1"])

    assert result.exit_code == 0
    assert "0/1 task(s) done" in result.output
