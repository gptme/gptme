"""Tests for skills hook system (Phase 4.2)."""

import tempfile
from pathlib import Path

import pytest

from gptme.lessons.hooks import HookContext, HookManager, get_hook_manager
from gptme.lessons.parser import Lesson, LessonMetadata


@pytest.fixture
def temp_skill_dir():
    """Create a temporary directory for test skills."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_skill(temp_skill_dir):
    """Create a sample skill with hooks."""
    skill_dir = temp_skill_dir / "test-skill"
    skill_dir.mkdir()

    # Create skill file
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: test-skill
description: Test skill with hooks
hooks:
  pre_execute: hooks/pre_execute.py
  post_execute: hooks/post_execute.py
---

# Test Skill
A test skill for hook system.
"""
    )

    # Create hooks directory
    hooks_dir = skill_dir / "hooks"
    hooks_dir.mkdir()

    # Create pre_execute hook
    (hooks_dir / "pre_execute.py").write_text(
        """
def execute(context):
    with open('/tmp/hook_pre_execute_called', 'w') as f:
        f.write(context.skill.title)
"""
    )

    # Create post_execute hook
    (hooks_dir / "post_execute.py").write_text(
        """
def execute(context):
    with open('/tmp/hook_post_execute_called', 'w') as f:
        f.write(context.skill.title)
"""
    )

    # Parse skill
    from gptme.lessons.parser import parse_lesson

    skill = parse_lesson(skill_file)
    return skill


@pytest.fixture
def hook_manager():
    """Create a fresh hook manager for each test."""
    manager = HookManager()
    yield manager
    manager.clear_hooks()


class TestHookContext:
    """Tests for HookContext dataclass."""

    def test_hook_context_creation(self, sample_skill):
        """Test creating HookContext with required fields."""
        context = HookContext(skill=sample_skill)
        assert context.skill == sample_skill
        assert context.message is None
        assert context.conversation is None

    def test_hook_context_with_optional_fields(self, sample_skill):
        """Test HookContext with optional fields."""
        context = HookContext(
            skill=sample_skill,
            message="test message",
            extra={"key": "value"},
        )
        assert context.message == "test message"
        assert context.extra == {"key": "value"}


class TestHookManager:
    """Tests for HookManager class."""

    def test_hook_manager_initialization(self, hook_manager):
        """Test HookManager initializes with empty hooks."""
        counts = hook_manager.get_registered_hooks()
        assert all(count == 0 for count in counts.values())

    def test_register_skill_hooks(self, hook_manager, sample_skill):
        """Test registering hooks from a skill."""
        hook_manager.register_skill_hooks(sample_skill)

        counts = hook_manager.get_registered_hooks()
        assert counts["pre_execute"] == 1
        assert counts["post_execute"] == 1
        assert counts["on_error"] == 0

    def test_register_invalid_hook_type(self, hook_manager, temp_skill_dir):
        """Test registering skill with invalid hook type."""
        skill_file = temp_skill_dir / "SKILL.md"
        skill_file.write_text(
            """---
name: invalid-hook-skill
hooks:
  invalid_hook: hooks/invalid.py
---
# Invalid Hook Skill
"""
        )

        from gptme.lessons.parser import parse_lesson

        skill = parse_lesson(skill_file)
        hook_manager.register_skill_hooks(skill)

        # Should warn but not crash
        counts = hook_manager.get_registered_hooks()
        assert all(count == 0 for count in counts.values())

    def test_execute_hooks(self, hook_manager, sample_skill):
        """Test executing registered hooks."""
        hook_manager.register_skill_hooks(sample_skill)

        context = HookContext(skill=sample_skill)

        # Execute pre_execute hook
        errors = hook_manager.execute_hooks("pre_execute", context)
        assert len(errors) == 0

        # Check hook was called
        pre_file = Path("/tmp/hook_pre_execute_called")
        assert pre_file.exists()
        assert pre_file.read_text() == sample_skill.title
        pre_file.unlink()

        # Execute post_execute hook
        errors = hook_manager.execute_hooks("post_execute", context)
        assert len(errors) == 0

        # Check hook was called
        post_file = Path("/tmp/hook_post_execute_called")
        assert post_file.exists()
        assert post_file.read_text() == sample_skill.title
        post_file.unlink()

    def test_execute_nonexistent_hook(self, hook_manager, sample_skill):
        """Test executing hook type with no registered hooks."""
        context = HookContext(skill=sample_skill)
        errors = hook_manager.execute_hooks("on_error", context)
        assert len(errors) == 0

    def test_execute_invalid_hook_type(self, hook_manager, sample_skill):
        """Test executing invalid hook type raises error."""
        context = HookContext(skill=sample_skill)
        with pytest.raises(ValueError, match="Invalid hook type"):
            hook_manager.execute_hooks("invalid_type", context)

    def test_hook_error_handling(self, hook_manager, temp_skill_dir):
        """Test hooks with errors don't crash the system."""
        skill_dir = temp_skill_dir / "error-skill"
        skill_dir.mkdir()

        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            """---
name: error-skill
hooks:
  pre_execute: hooks/error_hook.py
---
# Error Skill
"""
        )

        hooks_dir = skill_dir / "hooks"
        hooks_dir.mkdir()

        # Create hook that raises error
        (hooks_dir / "error_hook.py").write_text(
            """
def execute(context):
    raise RuntimeError("Test error")
"""
        )

        from gptme.lessons.parser import parse_lesson

        skill = parse_lesson(skill_file)
        hook_manager.register_skill_hooks(skill)

        context = HookContext(skill=skill)
        errors = hook_manager.execute_hooks("pre_execute", context)

        # Error should be caught and returned
        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)

    def test_hook_missing_execute_function(self, hook_manager, temp_skill_dir):
        """Test hook without execute() function."""
        skill_dir = temp_skill_dir / "no-execute-skill"
        skill_dir.mkdir()

        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            """---
name: no-execute-skill
hooks:
  pre_execute: hooks/no_execute.py
---
# No Execute Skill
"""
        )

        hooks_dir = skill_dir / "hooks"
        hooks_dir.mkdir()

        # Create hook without execute function
        (hooks_dir / "no_execute.py").write_text(
            """
# No execute function
pass
"""
        )

        from gptme.lessons.parser import parse_lesson

        skill = parse_lesson(skill_file)
        hook_manager.register_skill_hooks(skill)

        context = HookContext(skill=skill)
        errors = hook_manager.execute_hooks("pre_execute", context)

        # Should not error, just log warning
        assert len(errors) == 0

    def test_clear_hooks(self, hook_manager, sample_skill):
        """Test clearing registered hooks."""
        hook_manager.register_skill_hooks(sample_skill)

        counts = hook_manager.get_registered_hooks()
        assert counts["pre_execute"] == 1

        hook_manager.clear_hooks()

        counts = hook_manager.get_registered_hooks()
        assert all(count == 0 for count in counts.values())

    def test_clear_specific_hook_type(self, hook_manager, sample_skill):
        """Test clearing specific hook type."""
        hook_manager.register_skill_hooks(sample_skill)

        hook_manager.clear_hooks("pre_execute")

        counts = hook_manager.get_registered_hooks()
        assert counts["pre_execute"] == 0
        assert counts["post_execute"] == 1  # Still registered

    def test_get_registered_hooks_specific_type(self, hook_manager, sample_skill):
        """Test getting count for specific hook type."""
        hook_manager.register_skill_hooks(sample_skill)

        counts = hook_manager.get_registered_hooks("pre_execute")
        assert counts == {"pre_execute": 1}

    def test_multiple_skills_same_hook(self, hook_manager, temp_skill_dir):
        """Test multiple skills registering same hook type."""
        # Create two skills
        for i in range(2):
            skill_dir = temp_skill_dir / f"skill-{i}"
            skill_dir.mkdir()

            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(
                f"""---
name: skill-{i}
hooks:
  pre_execute: hooks/pre_execute.py
---
# Skill {i}
"""
            )

            hooks_dir = skill_dir / "hooks"
            hooks_dir.mkdir()

            (hooks_dir / "pre_execute.py").write_text(
                f"""
def execute(context):
    with open('/tmp/hook_skill_{i}_called', 'w') as f:
        f.write('skill-{i}')
"""
            )

        from gptme.lessons.parser import parse_lesson

        # Register both skills
        for i in range(2):
            skill = parse_lesson(temp_skill_dir / f"skill-{i}" / "SKILL.md")
            hook_manager.register_skill_hooks(skill)

        counts = hook_manager.get_registered_hooks()
        assert counts["pre_execute"] == 2

        # Execute hooks - both should run
        context = HookContext(
            skill=parse_lesson(temp_skill_dir / "skill-0" / "SKILL.md")
        )
        errors = hook_manager.execute_hooks("pre_execute", context)
        assert len(errors) == 0

        # Check both hooks were called
        for i in range(2):
            hook_file = Path(f"/tmp/hook_skill_{i}_called")
            assert hook_file.exists()
            hook_file.unlink()


def test_global_hook_manager():
    """Test global hook manager instance."""
    manager1 = get_hook_manager()
    manager2 = get_hook_manager()
    assert manager1 is manager2  # Should be same instance
