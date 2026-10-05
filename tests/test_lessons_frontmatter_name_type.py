"""Non-string frontmatter ``name`` (YAML int/bool) must not crash lesson indexing."""

from pathlib import Path

import pytest

from gptme.lessons.index import LessonIndex
from gptme.lessons.parser import parse_lesson


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", "5"), ("true", "True"), ("2024-01-15", "2024-01-15")],
)
def test_non_string_name_is_coerced(tmp_path: Path, raw: str, expected: str):
    f = tmp_path / "SKILL.md"
    f.write_text(f"---\nname: {raw}\ndescription: d\n---\nbody\n")
    assert parse_lesson(f).metadata.name == expected


@pytest.mark.parametrize(("raw", "expected"), [("5", "5"), ("true", "True")])
def test_cursor_mdc_non_string_name_is_coerced(tmp_path: Path, raw: str, expected: str):
    f = tmp_path / "rule.mdc"
    f.write_text(f"---\nname: {raw}\ndescription: d\nglobs: ['*.py']\n---\nbody\n")
    assert parse_lesson(f).metadata.name == expected


def test_index_survives_non_string_skill_name(tmp_path: Path):
    skill = tmp_path / "skills" / "s"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: 5\ndescription: d\n---\nbody\n")
    index = LessonIndex(lesson_dirs=[tmp_path / "skills"])
    assert [lesson.metadata.name for lesson in index.lessons] == ["5"]
