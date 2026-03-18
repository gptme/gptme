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


def test_prompt_skills_summary_xml_escaping(tmp_path: Path, monkeypatch):
    """Test that skill descriptions with special XML characters are properly escaped and not split mid-entity."""
    skill_dir = tmp_path / "skills" / "xml-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    # Description with XML special chars and length near 77 chars to trigger mid-entity truncation bug
    # "&amp;" expands to 5 chars; if truncated post-escape, "&am" etc. could appear
    desc_with_specials = (
        "A skill using C++ & Python <template> for parsing things nicely"
    )
    skill_file.write_text(f"""---
name: xml-skill
description: {desc_with_specials}
---

# XML Skill

Content here.
""")

    from gptme.lessons.index import LessonIndex, clear_cache

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))
    clear_cache()

    msgs = list(prompt_skills_summary(tool_format="xml"))

    if msgs:
        content = msgs[0].content
        # Should contain escaped versions, not raw special chars inside XML attributes/tags
        assert (
            "&amp;" in content
            or "&" not in content.split("xml-skill")[1].split("</skill>")[0]
        )
        assert "&lt;" in content or "<template>" not in content
        # Must not have bare "&" or "<" inside element text (malformed XML)
        import xml.etree.ElementTree as ET

        ET.fromstring(content)  # Would raise if malformed


def test_prompt_skills_summary_xml_truncate_before_escape(tmp_path: Path, monkeypatch):
    """Test that truncation happens before XML escaping to prevent mid-entity cuts."""
    skill_dir = tmp_path / "skills" / "trunc-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    # Place '&' at position 76 so truncation-then-escape never splits "&amp;"
    # but escape-then-truncation would produce "&am" if & fell at pos 75-77
    desc = "A" * 74 + "& rest of description that is long enough to trigger truncation"
    skill_file.write_text(f"""---
name: trunc-skill
description: {desc}
---

# Trunc Skill

Content here.
""")

    from gptme.lessons.index import LessonIndex, clear_cache

    def mock_default_dirs():
        return [tmp_path / "skills"]

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(mock_default_dirs))
    clear_cache()

    msgs = list(prompt_skills_summary(tool_format="xml"))

    if msgs:
        content = msgs[0].content
        # Must not contain a partial XML entity like "&am" or "&l"
        assert "&am" not in content or "&amp;" in content
        assert "&lt" not in content or "&lt;" in content
        # Must be valid XML
        import xml.etree.ElementTree as ET

        ET.fromstring(content)
