"""Exercise the editor adapter with real, non-interactive subprocesses."""

import shlex
import sys
from pathlib import Path

import pytest

from gptme.util.useredit import edit_text_with_editor


def test_editor_command_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = tmp_path / "editor with spaces.py"
    recorded_path = tmp_path / "edited-path.txt"
    replacement = "after ; $(not-a-shell-command)"
    script.write_text(
        "import pathlib, sys\n"
        "path = pathlib.Path(sys.argv[3])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(path))\n"
        "path.write_text(sys.argv[2])\n"
    )
    monkeypatch.setenv(
        "EDITOR",
        shlex.join([sys.executable, str(script), str(recorded_path), replacement]),
    )

    assert edit_text_with_editor("before", "toml") == replacement
    temporary_file = Path(recorded_path.read_text())
    assert temporary_file.suffix == ".toml"
    assert not temporary_file.exists()


def test_editor_failure_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = tmp_path / "failing editor.py"
    recorded_path = tmp_path / "edited-path.txt"
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(sys.argv[2])\n"
        "sys.exit(3)\n"
    )
    monkeypatch.setenv(
        "EDITOR", shlex.join([sys.executable, str(script), str(recorded_path)])
    )

    with pytest.raises(RuntimeError, match="non-zero exit code: 3"):
        edit_text_with_editor("before")
    assert not Path(recorded_path.read_text()).exists()


def test_editor_without_arguments(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EDITOR", shlex.quote(sys.executable))
    initial_text = "# Valid Python that leaves the file unchanged\n"
    assert edit_text_with_editor(initial_text) == initial_text


def test_invalid_editor_quoting_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("gptme.util.useredit.tempfile.tempdir", str(tmp_path))
    monkeypatch.setenv("EDITOR", "vim '")
    with pytest.raises(ValueError, match="No closing quotation"):
        edit_text_with_editor("before")
    assert list(tmp_path.iterdir()) == []
