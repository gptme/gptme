"""Unit tests for the gptme-tutorial command validators."""

from pathlib import Path

import pytest

from gptme.cli.cmd_tutorial import (
    BUGGY_SCRIPT,
    README,
    _validate_fix_bug,
    _validate_summarize,
    _validate_write_test,
)


@pytest.fixture()
def tutorial_dir(tmp_path: Path) -> Path:
    """Temp dir with the standard tutorial files."""
    (tmp_path / "README.md").write_text(README)
    (tmp_path / "buggy.py").write_text(BUGGY_SCRIPT)
    return tmp_path


# --- _validate_summarize ---


def test_validate_summarize_pass(tutorial_dir: Path) -> None:
    passed, msg = _validate_summarize(tutorial_dir)
    assert passed, msg


def test_validate_summarize_fail_no_readme(tmp_path: Path) -> None:
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
