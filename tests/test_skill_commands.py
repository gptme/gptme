"""Tests for invoking skills as slash commands (/skill:<name>)."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gptme.commands.base import (
    CommandContext,
    _command_registry,
    get_commands_with_descriptions,
    get_user_commands,
    handle_cmd,
    register_command,
    unregister_command,
)
from gptme.lessons.index import LessonIndex, clear_cache
from gptme.lessons.skill_commands import (
    register_skill_commands,
    substitute_arguments,
    unregister_skill_commands,
)
from gptme.prompt_queue import drain_prompt_queue

DEMO_BODY = """# Demo Skill

Do the thing with: $ARGUMENTS

First arg: $ARGUMENTS[0]
Second arg: ${1}
Missing arg: $ARGUMENTS[9]
"""


def _write_skill(root: Path, name: str, description: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    )
    return skill_file


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    """Temp skills directory that LessonIndex() discovers by default."""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(lambda: [root]))
    clear_cache()
    yield root
    unregister_skill_commands()
    clear_cache()


@pytest.fixture
def manager(tmp_path: Path) -> MagicMock:
    mgr = MagicMock()
    mgr.logdir = tmp_path / "logs" / "conv"
    mgr.logdir.mkdir(parents=True)
    return mgr


def _ctx(manager: MagicMock, full_args: str = "") -> CommandContext:
    return CommandContext(args=full_args.split(), full_args=full_args, manager=manager)


def test_substitute_arguments():
    body = "all=$ARGUMENTS a0=$ARGUMENTS[0] a1=${1} missing=${5}"
    out = substitute_arguments(body, "foo bar", ["foo", "bar"])
    # Out-of-range ${N} stays unchanged rather than becoming empty string
    assert out == "all=foo bar a0=foo a1=bar missing=${5}"


def test_substitute_arguments_preserves_dollar_amounts():
    # Plain $N without curly braces is never matched — dollar amounts are safe.
    # ${N} is the placeholder syntax; $100 (no braces) is prose and untouched.
    body = "Set budget to $100 and limit to $ARGUMENTS[99]."
    out = substitute_arguments(body, "", [])
    assert out == "Set budget to $100 and limit to $ARGUMENTS[99]."

    # Even with 101 args, $100 in prose is NOT substituted (no braces → no match).
    # $ARGUMENTS[99] IS substituted correctly (index 99 is in range with 101 args).
    many_args = [str(i) for i in range(101)]
    out2 = substitute_arguments(body, " ".join(many_args), many_args)
    assert out2 == "Set budget to $100 and limit to 99."


def test_substitute_arguments_word_boundary():
    # $ARGUMENTS adjacent to another word char must not partially match
    body = "Use $ARGUMENTSvar and $ARGUMENTS normally"
    out = substitute_arguments(body, "x", ["x"])
    assert out == "Use $ARGUMENTSvar and x normally"


def test_register_skill_commands_registers_canonical_and_alias(skills_root: Path):
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)

    registered = register_skill_commands()

    assert "skill:demo" in registered
    assert "demo" in registered
    assert "skill:demo" in _command_registry
    assert "demo" in _command_registry
    assert _command_registry["demo"] is _command_registry["skill:demo"]

    # Description surfaces in /help via the handler docstring (alias deduped)
    descs = dict(get_commands_with_descriptions())
    assert descs["skill:demo"] == "A demo skill"
    assert "demo" not in descs

    # Tab-completion source includes the prefixed form
    assert "/skill:demo" in get_user_commands()


def test_skill_handler_queues_substituted_prompt(skills_root: Path, manager):
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)
    register_skill_commands()

    handler = _command_registry["skill:demo"]
    yielded = list(handler(_ctx(manager, "foo bar")))
    assert yielded == []

    # Command message is undone (like built-in commands)
    manager.undo.assert_called_once_with(1, quiet=True)

    drained = drain_prompt_queue(manager.logdir)
    assert len(drained) == 1
    msg = drained[0]
    assert msg.role == "user"
    assert msg.content.startswith("Skill invoked: /skill:demo foo bar")
    assert "Do the thing with: foo bar" in msg.content
    assert "First arg: foo" in msg.content
    assert "Second arg: bar" in msg.content
    # Out-of-range $ARGUMENTS[N] is preserved, not replaced with empty string
    assert "Missing arg: $ARGUMENTS[9]" in msg.content
    # (bare $ARGUMENTS was substituted — verified implicitly by line 120)

    # Queue is drained: nothing left
    assert drain_prompt_queue(manager.logdir) == []


def test_skill_invocation_via_handle_cmd(skills_root: Path, manager):
    """End-to-end: /skill:demo dispatches through handle_cmd to the queue."""
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)
    register_skill_commands()

    assert list(handle_cmd("/skill:demo hello", manager)) == []
    drained = drain_prompt_queue(manager.logdir)
    assert len(drained) == 1
    assert "Do the thing with: hello" in drained[0].content

    # Bare alias works too
    assert list(handle_cmd("/demo world", manager)) == []
    drained = drain_prompt_queue(manager.logdir)
    assert len(drained) == 1
    assert "Do the thing with: world" in drained[0].content


def test_skill_without_args(skills_root: Path, manager):
    _write_skill(skills_root, "demo", "A demo skill", "Body $ARGUMENTS end")
    register_skill_commands()

    list(_command_registry["skill:demo"](_ctx(manager)))
    drained = drain_prompt_queue(manager.logdir)
    assert drained[0].content == "Skill invoked: /skill:demo\n\nBody  end"


def test_collision_with_existing_command_skips_bare_alias(skills_root: Path):
    _write_skill(skills_root, "help", "Shadows /help", "Never shown")

    # Make sure /help exists as a built-in command before registering
    from gptme import commands as _commands  # noqa: F401

    assert "help" in _command_registry
    original_help = _command_registry["help"]

    registered = register_skill_commands()

    assert "skill:help" in registered
    assert "help" not in registered
    assert _command_registry["help"] is original_help


def test_collision_with_loaded_tool_skips_bare_alias(skills_root: Path, monkeypatch):
    _write_skill(skills_root, "shell", "Shadows the shell tool", "Never shown")

    fake_tool = MagicMock()
    fake_tool.name = "shell"
    monkeypatch.setattr("gptme.tools.get_tools", lambda: [fake_tool])

    registered = register_skill_commands()

    assert "skill:shell" in registered
    assert "shell" not in registered
    assert "shell" not in _command_registry


def test_reregistration_is_idempotent_and_drops_stale(skills_root: Path):
    skill_file = _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)
    first = register_skill_commands()
    assert set(first) == {"skill:demo", "demo"}

    # Re-register: our own previous bare alias must not count as a collision
    second = register_skill_commands()
    assert set(second) == {"skill:demo", "demo"}

    # Remove the skill and re-register: stale commands are dropped
    skill_file.unlink()
    skill_file.parent.rmdir()
    clear_cache()
    third = register_skill_commands()
    assert third == []
    assert "skill:demo" not in _command_registry
    assert "demo" not in _command_registry


def test_canonical_does_not_clobber_foreign_skill_prefix_command(skills_root: Path):
    """A foreign command with 'skill:' prefix is not overwritten by our registration."""
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)

    def other(ctx):
        yield from ()

    register_command("skill:demo", other)
    try:
        registered = register_skill_commands()
        assert "skill:demo" not in registered
        assert _command_registry["skill:demo"] is other
    finally:
        unregister_command("skill:demo")


def test_reregistration_does_not_clobber_foreign_command(skills_root: Path):
    """A command registered by someone else under a skill's name is left alone."""
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)
    register_skill_commands()
    assert "demo" in _command_registry

    # Someone else (e.g. a tool) takes over the bare name after us
    def other(ctx):
        yield from ()

    register_command("demo", other)
    try:
        register_skill_commands()
        assert _command_registry["demo"] is other
        assert "skill:demo" in _command_registry
    finally:
        unregister_command("demo")


def test_register_never_raises_on_broken_index(monkeypatch):
    def boom():
        raise RuntimeError("broken skill dir")

    monkeypatch.setattr(LessonIndex, "_default_dirs", staticmethod(boom))
    assert register_skill_commands() == []


def test_lessons_without_name_are_not_registered(skills_root: Path):
    lesson_dir = skills_root / "plain"
    lesson_dir.mkdir()
    (lesson_dir / "plain.md").write_text(
        "---\nmatch:\n  keywords: [foo]\n---\n\n# Plain Lesson\n\nBody\n"
    )
    assert register_skill_commands() == []
