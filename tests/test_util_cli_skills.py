"""Tests for the gptme-util skills CLI commands."""

from pathlib import Path

from click.testing import CliRunner

from gptme.cli.util import main


def _create_skill(directory: Path, name: str, description: str) -> Path:
    """Create a test skill file."""
    skill_dir = directory / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / f"{name}.md"
    skill_file.write_text(
        f"""---
name: {name}
description: {description}
---

# {name}

Skill content for {name}.
"""
    )
    return skill_file


def _create_lesson(directory: Path, name: str, category: str = "tools") -> Path:
    """Create a test lesson file."""
    lesson_dir = directory / "lessons" / category
    lesson_dir.mkdir(parents=True, exist_ok=True)
    lesson_file = lesson_dir / f"{name}.md"
    lesson_file.write_text(
        f"""---
match:
  keywords:
    - "{name} keyword"
status: active
---

# {name.replace("-", " ").title()}

## Rule
Test rule for {name}.
"""
    )
    return lesson_file


def test_skills_list_empty(tmp_path, mocker):
    """Test skills list with no skills found."""
    # Point LessonIndex to empty directory
    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "nonexistent"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "list"])
    assert result.exit_code == 0
    assert "No skills or lessons found" in result.output


def test_skills_list_with_skills(tmp_path, mocker):
    """Test skills list showing discovered skills."""
    _create_skill(tmp_path, "test-skill", "A test skill for testing")
    _create_lesson(tmp_path, "test-lesson")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "skills", tmp_path / "lessons"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "list"])
    assert result.exit_code == 0
    assert "test-skill" in result.output
    assert "A test skill for testing" in result.output
    assert "1 lessons available" in result.output


def test_skills_list_all(tmp_path, mocker):
    """Test skills list --all showing skills and lessons."""
    _create_skill(tmp_path, "my-skill", "My skill description")
    _create_lesson(tmp_path, "my-lesson", "patterns")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "skills", tmp_path / "lessons"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "list", "--all"])
    assert result.exit_code == 0
    assert "my-skill" in result.output
    assert "My Lesson" in result.output
    assert "[patterns]" in result.output


def test_skills_list_json(tmp_path, mocker):
    """Test skills list --json output."""
    _create_skill(tmp_path, "json-skill", "JSON test")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "skills"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "list", "--json"])
    assert result.exit_code == 0

    import json

    data = json.loads(result.output)
    assert "skills" in data
    assert len(data["skills"]) == 1
    assert data["skills"][0]["name"] == "json-skill"


def test_skills_show(tmp_path, mocker):
    """Test skills show command."""
    _create_skill(tmp_path, "show-me", "Skill to show")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "skills"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "show", "show-me"])
    assert result.exit_code == 0
    assert "show-me" in result.output
    assert "Skill content" in result.output


def test_skills_show_lesson(tmp_path, mocker):
    """Test skills show for a lesson (not skill)."""
    _create_lesson(tmp_path, "findable-lesson")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "lessons"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "show", "findable-lesson"])
    assert result.exit_code == 0
    assert "Findable Lesson" in result.output


def test_skills_show_not_found(tmp_path, mocker):
    """Test skills show for nonexistent item."""
    # Create a skill so the index isn't empty (tests the "not found" path, not "empty index")
    _create_skill(tmp_path, "other-skill", "Other")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "skills"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "show", "nope"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_skills_search(tmp_path, mocker):
    """Test skills search command."""
    _create_skill(tmp_path, "python-repl", "Python REPL skill")
    _create_lesson(tmp_path, "shell-safety")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "skills", tmp_path / "lessons"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "search", "python"])
    assert result.exit_code == 0
    assert "python-repl" in result.output


def test_skills_search_by_metadata_name(tmp_path, mocker):
    """Test that search finds skills by metadata.name even when it differs from title."""
    # Create a skill where metadata.name differs from the H1 title
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "deploy.md").write_text(
        """---
name: deploy-helper
description: Automates deployment workflows
---

# Production Deployment Tool

Content about deployments.
"""
    )

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[skill_dir],
    )

    runner = CliRunner()
    # Search by metadata.name which doesn't appear in title
    result = runner.invoke(main, ["skills", "search", "deploy-helper"])
    assert result.exit_code == 0
    assert "deploy-helper" in result.output

    # Search by metadata.description
    result = runner.invoke(main, ["skills", "search", "deployment workflows"])
    assert result.exit_code == 0
    assert "deploy-helper" in result.output


def test_skills_search_no_results(tmp_path, mocker):
    """Test skills search with no matching results."""
    _create_skill(tmp_path, "unrelated", "Nothing to see")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[tmp_path / "skills"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "search", "zzzznotfound"])
    assert result.exit_code == 0
    assert "No results" in result.output


def test_skills_dirs(tmp_path, mocker):
    """Test skills dirs command."""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "test.md").write_text("# Test")

    mocker.patch(
        "gptme.lessons.index.LessonIndex._default_dirs",
        return_value=[skill_dir, tmp_path / "nonexistent"],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["skills", "dirs"])
    assert result.exit_code == 0
    assert "+" in result.output  # existing dir marker
    assert "-" in result.output  # missing dir marker
    assert "1 files" in result.output
    assert "not found" in result.output
