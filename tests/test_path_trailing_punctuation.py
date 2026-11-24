"""Explicit @paths followed by prose punctuation are still discovered."""

from pathlib import Path

import pytest

from gptme.message import Message
from gptme.util.context import (
    _find_potential_paths,
    _strip_trailing_prose_punctuation,
    extract_urls,
    include_paths,
)


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


def test_backtick_quoted_name_keeps_its_punctuation(workspace: Path) -> None:
    (workspace / "meeting notes)").write_text("quoted\n")
    assert _find_potential_paths("read `@meeting notes)`") == ["meeting notes)"]


@pytest.mark.parametrize(
    "url",
    ["https://example.com/search?q=end;", "https://example.com/wiki/Foo_(bar)"],
)
def test_url_keeps_query_and_paren_characters(workspace: Path, url: str) -> None:
    assert extract_urls(f"see {url}") == [url]
    assert extract_urls(f"see `{url}`") == [url]


@pytest.mark.parametrize(
    "prompt",
    ["Look at @main.py: it crashes", "(see @notes(1))", "fix @src/app.py; rerun"],
)
def test_explicit_reference_is_attached(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    (workspace / "notes(1)").write_text("NOTES-CONTENT\n")
    monkeypatch.delenv("GPTME_AUTO_INCLUDE_PATHS", raising=False)
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_FRESH", raising=False)
    expected = {
        "Look at @main.py: it crashes": "print('hi')",
        "(see @notes(1))": "NOTES-CONTENT",
        "fix @src/app.py; rerun": "x = 1",
    }[prompt]
    message = include_paths(Message("user", prompt), workspace)
    assert expected in message.content


def test_long_punctuation_run_is_linear() -> None:
    word = "@a.py" + ")" * 50_000 + ":" * 50_000
    assert _strip_trailing_prose_punctuation(word) == "@a.py"
