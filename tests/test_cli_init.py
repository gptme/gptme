"""Tests for `gptme-init` target validation."""

import os

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_init import main


def _assert_clean_error(result, expected: str) -> None:
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert not isinstance(result.exception, OSError), repr(result.exception)
    assert expected in result.output


def test_init_target_is_a_file(tmp_path):
    target = tmp_path / "afile"
    target.write_text("x")
    result = CliRunner().invoke(main, [str(target)])
    _assert_clean_error(result, "not a directory")
    assert target.read_text() == "x"


def test_init_parent_is_a_file(tmp_path):
    parent = tmp_path / "afile"
    parent.write_text("x")
    result = CliRunner().invoke(main, [str(parent / "sub")])
    _assert_clean_error(result, "Cannot create")


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permission bits are not enforced for root / on Windows",
)
def test_init_unwritable_parent(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        result = CliRunner().invoke(main, [str(ro / "sub")])
    finally:
        ro.chmod(0o755)
    _assert_clean_error(result, "Cannot create")


def test_init_creates_project_in_new_directory(tmp_path):
    target = tmp_path / "proj"
    result = CliRunner().invoke(main, [str(target)])
    assert result.exit_code == 0, result.output
    assert (target / "gptme.toml").exists()
