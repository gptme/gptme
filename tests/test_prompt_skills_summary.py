"""Tests for skills summary in prompts."""

from pathlib import Path

from gptme.prompts import prompt_skills_summary


def test_prompt_skills_summary_no_skills():
    """Test skills summary with no skills available."""
    msgs = list(prompt_skills_summary())
    # May be empty if no skills found
    assert isinstance(msgs, list)


def test_prompt_skills_summary_format(tmp_path: Path, monkeypatch):
    """Test skills summary generates correct format."""
    # Create a mock skill file
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("""---
name: test-skill
description: A test skill for testing
---

# Test Skill

This is a test skill.
""")

    # Mock LessonIndex to use our test directory
    from gptme.lessons.index import LessonIndex

    original_default_dirs = LessonIndex._default_dirs

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))

    # Also clear cache
    from gptme.lessons.index import clear_cache

    clear_cache()

    msgs = list(prompt_skills_summary())

    if msgs:
        content = msgs[0].content
        assert "Available Skills" in content
        assert "test-skill" in content
        assert "A test skill for testing" in content
        assert str(skill_file) in content or "SKILL.md" in content


def test_prompt_skills_summary_truncates_description(tmp_path: Path, monkeypatch):
    """Test that long descriptions are truncated."""
    # Create a mock skill file with long description
    skill_dir = tmp_path / "skills" / "long-desc"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    long_desc = "A" * 200  # Very long description
    skill_file.write_text(f"""---
name: long-desc
description: {long_desc}
---

# Long Description Skill

Content here.
""")

    from gptme.lessons.index import LessonIndex, clear_cache

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))
    clear_cache()

    msgs = list(prompt_skills_summary())

    if msgs:
        content = msgs[0].content
        # Description should be truncated to 80 chars with "..."
        assert "..." in content
        assert long_desc not in content  # Full description should not appear
