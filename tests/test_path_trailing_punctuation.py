"""Explicit @paths followed by prose punctuation are still discovered."""

from pathlib import Path

import pytest

from gptme.util.context import _find_potential_paths


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "main.py").write_text("print('hi')\n")
    (tmp_path / "notes(1).md").write_text("n\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Look at @main.py: it crashes", "main.py"),
        ("fix @src/app.py: it crashes", "src/app.py"),
        ("fix @src/app.py; then rerun", "src/app.py"),
        ("(see @src/app.py)", "src/app.py"),
        ("did you read @main.py?!", "main.py"),
        ("Look at @main.py, it crashes", "main.py"),
    ],
)
def test_trailing_punctuation_is_not_part_of_the_path(
    workspace: Path, prompt: str, expected: str
) -> None:
    assert _find_potential_paths(prompt) == [expected]


def test_balanced_parenthesis_in_filename_is_kept(workspace: Path) -> None:
    assert _find_potential_paths("open @notes(1).md") == ["notes(1).md"]
