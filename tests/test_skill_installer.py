"""Tests for the skill installer module."""

from pathlib import Path

import pytest

from gptme.lessons.installer import (
    InstalledSkill,
    SkillManifest,
    check_dependencies,
    dependency_graph,
    get_manifest,
    get_skills_dir,
    init_skill,
    install_skill,
    list_installed,
    publish_skill,
    uninstall_skill,
    validate_skill,
)


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Create a valid skill directory."""
    skill = tmp_path / "test-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: test-skill
description: A test skill for unit testing
metadata:
  author: test
  version: "1.0.0"
  tags: "test,unit"
---

# Test Skill

This is a test skill.

## Instructions

Do the thing.
"""
    )
    return skill


@pytest.fixture
def skill_dir_minimal(tmp_path: Path) -> Path:
    """Create a minimal skill directory (no marketplace metadata)."""
    skill = tmp_path / "minimal-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
name: minimal-skill
description: Minimal skill
---

# Minimal Skill

Just the basics.
"""
    )
    return skill


@pytest.fixture
def skill_dir_invalid(tmp_path: Path) -> Path:
    """Create an invalid skill directory (missing required fields)."""
    skill = tmp_path / "bad-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        """---
status: active
---

# Bad Skill

No name or description.
"""
    )
    return skill


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use a temporary config directory for all tests."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr("gptme.lessons.installer.get_config_dir", lambda: config_dir)


class TestValidateSkill:
    def test_valid_skill(self, skill_dir: Path):
        errors = validate_skill(skill_dir)
        assert errors == []

    def test_valid_skill_from_file(self, skill_dir: Path):
        errors = validate_skill(skill_dir / "SKILL.md")
        assert errors == []

    def test_minimal_skill_has_warnings(self, skill_dir_minimal: Path):
        errors = validate_skill(skill_dir_minimal)
        # Should warn about missing marketplace metadata
        assert any("metadata.author" in e for e in errors)
        assert any("metadata.version" in e for e in errors)
        assert any("metadata.tags" in e for e in errors)

    def test_invalid_skill_missing_fields(self, skill_dir_invalid: Path):
        errors = validate_skill(skill_dir_invalid)
        assert any("name" in e for e in errors)
        assert any("description" in e for e in errors)

    def test_missing_skill_md(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        errors = validate_skill(empty)
        assert any("SKILL.md" in e for e in errors)


class TestInstallSkill:
    def test_install_from_local_path(self, skill_dir: Path):
        success, msg = install_skill(str(skill_dir))
        assert success, msg
        assert "test-skill" in msg

        # Verify it's in the skills directory
        installed_dir = get_skills_dir() / "test-skill"
        assert installed_dir.exists()
        assert (installed_dir / "SKILL.md").exists()

    def test_install_with_name_override(self, skill_dir: Path):
        success, msg = install_skill(str(skill_dir), name="custom-name")
        assert success, msg

        installed_dir = get_skills_dir() / "custom-name"
        assert installed_dir.exists()

    def test_install_duplicate_fails(self, skill_dir: Path):
        success, _ = install_skill(str(skill_dir))
        assert success

        success, msg = install_skill(str(skill_dir))
        assert not success
        assert "already installed" in msg

    def test_install_force_overwrites(self, skill_dir: Path):
        success, _ = install_skill(str(skill_dir))
        assert success

        success, msg = install_skill(str(skill_dir), force=True)
        assert success

    def test_install_updates_manifest(self, skill_dir: Path):
        install_skill(str(skill_dir))
        manifest = get_manifest()
        assert "test-skill" in manifest.skills
        assert manifest.skills["test-skill"].version == "1.0.0"
        assert manifest.skills["test-skill"].source == str(skill_dir)

    def test_install_nonexistent_path(self):
        success, msg = install_skill("/nonexistent/path")
        assert not success
        # Falls through to registry lookup which fails on clone
        assert "Failed" in msg or "not found" in msg


class TestUninstallSkill:
    def test_uninstall_installed_skill(self, skill_dir: Path):
        install_skill(str(skill_dir))

        success, msg = uninstall_skill("test-skill")
        assert success
        assert not (get_skills_dir() / "test-skill").exists()

        # Verify manifest updated
        manifest = get_manifest()
        assert "test-skill" not in manifest.skills

    def test_uninstall_nonexistent(self):
        success, msg = uninstall_skill("nonexistent")
        assert not success
        assert "not found" in msg

    def test_uninstall_directory_without_manifest(self):
        """Uninstall works even if skill isn't in manifest."""
        skill_dir = get_skills_dir() / "orphan-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: orphan\n---\n# Orphan")

        success, msg = uninstall_skill("orphan-skill")
        assert success
        assert not skill_dir.exists()


class TestListInstalled:
    def test_empty_list(self):
        installed = list_installed()
        assert installed == []

    def test_list_after_install(self, skill_dir: Path):
        install_skill(str(skill_dir))
        installed = list_installed()
        assert len(installed) == 1
        assert installed[0].name == "test-skill"

    def test_list_multiple(self, skill_dir: Path, skill_dir_minimal: Path):
        install_skill(str(skill_dir))
        install_skill(str(skill_dir_minimal))
        installed = list_installed()
        assert len(installed) == 2
        names = {s.name for s in installed}
        assert names == {"test-skill", "minimal-skill"}


class TestSkillManifest:
    def test_save_and_load(self, tmp_path: Path):
        manifest = SkillManifest()
        manifest.skills["test"] = InstalledSkill(
            name="test",
            version="1.0.0",
            source="local",
            install_path="/tmp/test",
            installed_at="2026-01-01T00:00:00Z",
        )

        path = tmp_path / "manifest.yaml"
        manifest.save(path)

        loaded = SkillManifest.load(path)
        assert "test" in loaded.skills
        assert loaded.skills["test"].version == "1.0.0"

    def test_load_missing_file(self, tmp_path: Path):
        manifest = SkillManifest.load(tmp_path / "nonexistent.yaml")
        assert manifest.skills == {}

    def test_load_corrupted_file(self, tmp_path: Path):
        path = tmp_path / "bad.yaml"
        path.write_text("not: valid: yaml: {{}")
        manifest = SkillManifest.load(path)
        assert manifest.skills == {}


class TestInitSkill:
    def test_init_creates_skill(self, tmp_path: Path):
        target = tmp_path / "new-skill"
        success, msg = init_skill(target, name="new-skill")
        assert success, msg
        assert target.exists()
        assert (target / "SKILL.md").exists()

    def test_init_content_valid(self, tmp_path: Path):
        target = tmp_path / "my-skill"
        init_skill(target, name="my-skill", description="Does stuff", author="bob")

        # The created skill should pass validation
        errors = validate_skill(target)
        # Only warnings about tags expected, not real errors
        real_errors = [e for e in errors if "recommended" not in e.lower()]
        assert real_errors == [], f"Unexpected errors: {real_errors}"

    def test_init_with_all_options(self, tmp_path: Path):
        target = tmp_path / "full-skill"
        success, msg = init_skill(
            target,
            name="full-skill",
            description="A complete skill",
            author="bob",
            tags="coding, testing",
        )
        assert success, msg

        content = (target / "SKILL.md").read_text()
        assert "full-skill" in content
        assert "A complete skill" in content
        assert "bob" in content
        assert "coding" in content

    def test_init_defaults_name_to_dirname(self, tmp_path: Path):
        target = tmp_path / "auto-named"
        success, msg = init_skill(target)
        assert success, msg
        content = (target / "SKILL.md").read_text()
        assert "auto-named" in content

    def test_init_fails_if_skill_exists(self, skill_dir: Path):
        success, msg = init_skill(skill_dir)
        assert not success
        assert "already exists" in msg

    def test_init_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "skill"
        success, msg = init_skill(target)
        assert success, msg
        assert (target / "SKILL.md").exists()


class TestPublishSkill:
    def test_publish_valid_skill(self, skill_dir: Path):
        success, msg, archive = publish_skill(skill_dir)
        assert success, msg
        assert archive is not None
        assert archive.exists()
        assert archive.name == "test-skill-1.0.0.tar.gz"

    def test_publish_archive_contents(self, skill_dir: Path):
        import tarfile

        success, _, archive = publish_skill(skill_dir)
        assert success
        assert archive is not None

        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            assert "test-skill/SKILL.md" in names

    def test_publish_includes_extra_files(self, skill_dir: Path):
        import tarfile

        # Add a supporting script
        (skill_dir / "helper.sh").write_text("#!/bin/bash\necho hello")

        success, _, archive = publish_skill(skill_dir)
        assert success
        assert archive is not None

        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            assert "test-skill/helper.sh" in names
            assert "test-skill/SKILL.md" in names

    def test_publish_excludes_hidden_files(self, skill_dir: Path):
        import tarfile

        (skill_dir / ".git").mkdir()
        (skill_dir / ".git" / "config").write_text("stuff")
        (skill_dir / "__pycache__").mkdir()
        (skill_dir / "__pycache__" / "foo.pyc").write_text("bytecode")

        success, _, archive = publish_skill(skill_dir)
        assert success
        assert archive is not None

        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            assert not any(".git" in n for n in names)
            assert not any("__pycache__" in n for n in names)

    def test_publish_invalid_skill_fails(self, skill_dir_invalid: Path):
        success, msg, archive = publish_skill(skill_dir_invalid)
        assert not success
        assert "validation failed" in msg.lower()
        assert archive is None

    def test_publish_missing_skill_md(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        success, msg, archive = publish_skill(empty)
        assert not success
        assert archive is None

    def test_publish_from_skill_md_file(self, skill_dir: Path):
        success, msg, archive = publish_skill(skill_dir / "SKILL.md")
        assert success, msg
        assert archive is not None

    def test_publish_shows_instructions(self, skill_dir: Path):
        success, msg, _ = publish_skill(skill_dir)
        assert success
        assert "gptme-contrib" in msg
        assert "gptme-util skills install" in msg

    def test_publish_minimal_has_warnings(self, skill_dir_minimal: Path):
        success, msg, archive = publish_skill(skill_dir_minimal)
        assert success, msg
        assert archive is not None
        # Minimal skills should publish but show warnings
        assert "Warnings" in msg or "metadata" in msg.lower()


class TestSkillDependencies:
    """Tests for skill dependency declarations."""

    def test_valid_depends_in_skill(self, tmp_path: Path):
        """Skill with valid depends list should pass validation."""
        skill = tmp_path / "dep-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            """---
name: dep-skill
description: A skill with dependencies
depends:
  - other-skill
  - another-skill
metadata:
  author: test
  version: "1.0.0"
  tags: test
---

# Dep Skill

Needs other skills.
"""
        )
        errors = validate_skill(skill)
        real_errors = [e for e in errors if "recommended" not in e.lower()]
        assert not real_errors, f"Unexpected errors: {real_errors}"

    def test_depends_string_auto_converts(self, tmp_path: Path):
        """Single depends string should be accepted (auto-converts to list)."""
        skill = tmp_path / "single-dep"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            """---
name: single-dep
description: Skill with single string depends
depends: other-skill
metadata:
  author: test
  version: "1.0.0"
  tags: test
---

# Single Dep
"""
        )
        errors = validate_skill(skill)
        real_errors = [e for e in errors if "recommended" not in e.lower()]
        assert not real_errors, f"Unexpected errors: {real_errors}"

    def test_invalid_depends_type(self, tmp_path: Path):
        """Non-list/non-string depends should fail validation."""
        skill = tmp_path / "bad-deps"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            """---
name: bad-deps
description: Skill with invalid depends
depends: 42
metadata:
  author: test
  version: "1.0.0"
  tags: test
---

# Bad Deps
"""
        )
        errors = validate_skill(skill)
        dep_errors = [e for e in errors if "depends" in e.lower()]
        assert any("must be a list" in e for e in dep_errors)

    def test_invalid_depends_characters(self, tmp_path: Path):
        """Dependency with invalid characters should fail."""
        skill = tmp_path / "char-deps"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            """---
name: char-deps
description: Skill with bad dependency names
depends:
  - "../evil"
  - "has spaces"
metadata:
  author: test
  version: "1.0.0"
  tags: test
---

# Char Deps
"""
        )
        errors = validate_skill(skill)
        dep_errors = [
            e for e in errors if "depends" in e.lower() or "invalid" in e.lower()
        ]
        assert len(dep_errors) >= 2, f"Expected 2+ dependency errors, got: {dep_errors}"

    def test_empty_depends_entry(self, tmp_path: Path):
        """Empty string dependency should fail."""
        skill = tmp_path / "empty-dep"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            """---
name: empty-dep
description: Skill with empty dep
depends:
  - ""
metadata:
  author: test
  version: "1.0.0"
  tags: test
---

# Empty Dep
"""
        )
        errors = validate_skill(skill)
        dep_errors = [e for e in errors if "Invalid dependency" in e]
        assert len(dep_errors) == 1

    def test_no_depends_is_valid(self, skill_dir: Path):
        """Skill without depends field should be valid."""
        errors = validate_skill(skill_dir)
        real_errors = [e for e in errors if "recommended" not in e.lower()]
        assert not real_errors

    def test_dependency_graph_from_index(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """dependency_graph should return skills with dependencies."""
        from gptme.lessons.index import LessonIndex
        from gptme.lessons.parser import parse_lesson

        skill1 = tmp_path / "skill-a"
        skill1.mkdir()
        (skill1 / "SKILL.md").write_text(
            """---
name: skill-a
description: Depends on skill-b
depends:
  - skill-b
---

# Skill A
"""
        )
        skill2 = tmp_path / "skill-b"
        skill2.mkdir()
        (skill2 / "SKILL.md").write_text(
            """---
name: skill-b
description: No dependencies
---

# Skill B
"""
        )

        parsed_a = parse_lesson(skill1 / "SKILL.md")
        parsed_b = parse_lesson(skill2 / "SKILL.md")

        # Monkeypatch LessonIndex and manifest to use our test skills
        fake_index = LessonIndex.__new__(LessonIndex)
        fake_index.lessons = [parsed_a, parsed_b]
        monkeypatch.setattr("gptme.lessons.index.LessonIndex", lambda: fake_index)
        monkeypatch.setattr(
            "gptme.lessons.installer.get_manifest", lambda: SkillManifest()
        )

        graph = dependency_graph()

        assert graph == {"skill-a": ["skill-b"]}
        assert "skill-b" not in graph  # No deps, not included

    def test_depends_with_dots_and_hyphens(self, tmp_path: Path):
        """Dependencies with dots and hyphens should be valid."""
        skill = tmp_path / "deps-dots"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            """---
name: deps-dots
description: Skill with dotted dependencies
depends:
  - org.other-skill
  - my-skill.v2
metadata:
  author: test
  version: "1.0.0"
  tags: test
---

# Dots Deps
"""
        )
        errors = validate_skill(skill)
        real_errors = [e for e in errors if "recommended" not in e.lower()]
        assert not real_errors, f"Unexpected errors: {real_errors}"

    def test_dependency_graph_raises_on_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """dependency_graph() should raise ValueError when circular deps exist."""
        from gptme.lessons.index import LessonIndex
        from gptme.lessons.parser import parse_lesson

        skill_a = tmp_path / "cycle-a"
        skill_a.mkdir()
        (skill_a / "SKILL.md").write_text(
            "---\nname: cycle-a\ndescription: Cycle A\ndepends:\n  - cycle-b\n---\n# Cycle A\n"
        )
        skill_b = tmp_path / "cycle-b"
        skill_b.mkdir()
        (skill_b / "SKILL.md").write_text(
            "---\nname: cycle-b\ndescription: Cycle B\ndepends:\n  - cycle-a\n---\n# Cycle B\n"
        )

        parsed_a = parse_lesson(skill_a / "SKILL.md")
        parsed_b = parse_lesson(skill_b / "SKILL.md")

        # Monkeypatch LessonIndex at the source module
        fake_index = LessonIndex.__new__(LessonIndex)
        fake_index.lessons = [parsed_a, parsed_b]
        monkeypatch.setattr("gptme.lessons.index.LessonIndex", lambda: fake_index)

        with pytest.raises(ValueError, match="Circular"):
            dependency_graph()

    def test_check_dependencies_includes_manifest_only_skills(
        self, tmp_path: Path, skill_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """check_dependencies(None) should check manifest-only skills too."""
        from gptme.lessons.index import LessonIndex

        # Install a skill so it's in the manifest but not the index
        install_skill(str(skill_dir))

        # Mock an empty index (no indexed lessons)
        fake_index = LessonIndex.__new__(LessonIndex)
        fake_index.lessons = []
        monkeypatch.setattr("gptme.lessons.index.LessonIndex", lambda: fake_index)

        # Patch the installed skill's SKILL.md to declare a missing dependency
        installed_md = get_skills_dir() / "test-skill" / "SKILL.md"
        installed_md.write_text(
            "---\nname: test-skill\ndescription: Test\ndepends:\n  - missing-dep\n---\n# Test\n"
        )

        missing = check_dependencies()
        assert any(m["skill"] == "test-skill" for m in missing), (
            f"Manifest-only skill not checked: {missing}"
        )

    def test_check_dependencies_raises_on_unknown_skill(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """check_dependencies() should raise KeyError for unknown skill names."""
        from gptme.lessons.index import LessonIndex

        fake_index = LessonIndex.__new__(LessonIndex)
        fake_index.lessons = []
        monkeypatch.setattr("gptme.lessons.index.LessonIndex", lambda: fake_index)
        monkeypatch.setattr(
            "gptme.lessons.installer.get_manifest", lambda: SkillManifest()
        )

        with pytest.raises(KeyError, match="Unknown skill"):
            check_dependencies(["nonexistent-skill"])

    def test_check_dependencies_explicit_skill_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """check_dependencies() with explicit names returns satisfied deps."""
        from gptme.lessons.index import LessonIndex
        from gptme.lessons.parser import parse_lesson

        skill = tmp_path / "has-dep"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: has-dep\ndescription: D\ndepends:\n  - other\n---\n# D\n"
        )
        other = tmp_path / "other"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: other\ndescription: O\n---\n# O\n")

        parsed_dep = parse_lesson(skill / "SKILL.md")
        parsed_other = parse_lesson(other / "SKILL.md")

        fake_index = LessonIndex.__new__(LessonIndex)
        fake_index.lessons = [parsed_dep, parsed_other]
        monkeypatch.setattr("gptme.lessons.index.LessonIndex", lambda: fake_index)
        monkeypatch.setattr(
            "gptme.lessons.installer.get_manifest", lambda: SkillManifest()
        )

        # All deps satisfied — should return empty
        missing = check_dependencies(["has-dep"])
        assert missing == []

    def test_depends_null_does_not_crash_check_dependencies(
        self, tmp_path: Path, skill_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """depends: null (YAML) should not crash check_dependencies."""
        from gptme.lessons.index import LessonIndex

        install_skill(str(skill_dir))

        fake_index = LessonIndex.__new__(LessonIndex)
        fake_index.lessons = []
        monkeypatch.setattr("gptme.lessons.index.LessonIndex", lambda: fake_index)

        # Patch SKILL.md to have depends: null
        installed_md = get_skills_dir() / "test-skill" / "SKILL.md"
        installed_md.write_text(
            "---\nname: test-skill\ndescription: Test\ndepends:\n---\n# Test\n"
        )

        # Should not crash — depends: (bare) parses as None in YAML
        missing = check_dependencies()
        assert isinstance(missing, list)

    def test_depends_null_does_not_crash_dependency_graph(
        self, tmp_path: Path, skill_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """depends: null (YAML) should not crash dependency_graph."""
        from gptme.lessons.index import LessonIndex

        install_skill(str(skill_dir))

        fake_index = LessonIndex.__new__(LessonIndex)
        fake_index.lessons = []
        monkeypatch.setattr("gptme.lessons.index.LessonIndex", lambda: fake_index)

        installed_md = get_skills_dir() / "test-skill" / "SKILL.md"
        installed_md.write_text(
            "---\nname: test-skill\ndescription: Test\ndepends:\n---\n# Test\n"
        )

        # Should not crash
        graph = dependency_graph()
        assert isinstance(graph, dict)

    def test_depends_null_does_not_crash_parse_lesson(self, tmp_path: Path):
        """depends: null should not crash parse_lesson."""
        from gptme.lessons.parser import parse_lesson

        skill = tmp_path / "null-dep"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: null-dep\ndescription: Null\ndepends:\n---\n# Null\n"
        )

        parsed = parse_lesson(skill / "SKILL.md")
        assert parsed.metadata.depends == []

    def test_depends_integer_does_not_crash_parse_lesson(self, tmp_path: Path):
        """depends: 42 should not crash parse_lesson."""
        from gptme.lessons.parser import parse_lesson

        skill = tmp_path / "int-dep"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: int-dep\ndescription: Int\ndepends: 42\n---\n# Int\n"
        )

        parsed = parse_lesson(skill / "SKILL.md")
        assert parsed.metadata.depends == []
