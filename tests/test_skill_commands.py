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
    is_skill_command,
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


def test_substitute_arguments_non_numeric_bracket_not_matched():
    # $ARGUMENTS[name] (non-numeric bracket) must NOT be matched by the bare
    # $ARGUMENTS alternative — the '[' lookahead prevents this.
    body = "Use $ARGUMENTS[name] as a label"
    out = substitute_arguments(body, "hello", ["hello"])
    assert out == "Use $ARGUMENTS[name] as a label"


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


def test_collision_with_loaded_tool_is_case_insensitive(skills_root: Path, monkeypatch):
    """A skill named 'Shell' (capital S) must not shadow the 'shell' tool."""
    _write_skill(skills_root, "Shell", "Case-variant of shell tool", "Never shown")

    fake_tool = MagicMock()
    fake_tool.name = "shell"
    monkeypatch.setattr("gptme.tools.get_tools", lambda: [fake_tool])

    registered = register_skill_commands()

    assert "skill:Shell" in registered
    assert "Shell" not in registered
    assert "Shell" not in _command_registry


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


def test_handler_undo_happens_after_build_on_success(skills_root: Path, manager):
    """The command message is only removed from the log when build succeeds, not before."""
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)
    register_skill_commands()
    list(_command_registry["skill:demo"](_ctx(manager, "hello world")))
    # manager.undo must have been called (success path removes the command message)
    manager.undo.assert_called_once_with(1, quiet=True)


def test_handler_undo_not_called_when_build_fails(
    skills_root: Path, manager, monkeypatch
):
    """When build_skill_prompt raises, the log is left intact (undo not called)."""
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)
    register_skill_commands()

    import gptme.lessons.skill_commands as sc

    monkeypatch.setattr(
        sc,
        "build_skill_prompt",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    list(_command_registry["skill:demo"](_ctx(manager, "")))
    # manager.undo must NOT have been called — the log stays intact so the user
    # can see the failed invocation instead of a silent disappear.
    manager.undo.assert_not_called()


def test_two_pass_registration_skill_named_with_prefix(skills_root: Path, manager):
    """Skill 'X' keeps its canonical when another skill is named 'skill:X'.

    Pass 2 must not register the prefix-named skill's bare alias over
    /skill:X — that name is already the canonical of skill X.
    """
    _write_skill(skills_root, "skill:demo", "Weird name", "Body of prefix skill")
    _write_skill(skills_root, "demo", "Normal skill", "Body of demo skill")
    registered = register_skill_commands()

    assert "skill:demo" in registered
    assert "skill:skill:demo" in registered
    assert "demo" in registered

    # Handler identity: /skill:demo belongs to the 'demo' skill, not the
    # prefix-named one. Membership alone would pass even if pass 2 clobbered
    # the canonical with the wrong handler.
    assert _command_registry["skill:demo"].__name__ == "skill_demo"
    assert _command_registry["skill:skill:demo"].__name__ == "skill_skill:demo"
    assert _command_registry["demo"] is _command_registry["skill:demo"]

    list(_command_registry["skill:demo"](_ctx(manager, "")))
    drained = drain_prompt_queue(manager.logdir)
    assert len(drained) == 1
    assert "Body of demo skill" in drained[0].content
    assert drained[0].content.startswith("Skill invoked: /skill:demo")

    list(_command_registry["skill:skill:demo"](_ctx(manager, "")))
    drained = drain_prompt_queue(manager.logdir)
    assert len(drained) == 1
    assert "Body of prefix skill" in drained[0].content
    assert drained[0].content.startswith("Skill invoked: /skill:skill:demo")


def test_tool_list_unavailable_suppresses_all_bare_aliases(
    skills_root: Path, monkeypatch
):
    """When _loaded_tool_names() returns the sentinel (get_tools raised), no bare aliases are registered."""
    _write_skill(skills_root, "demo", "A demo skill", DEMO_BODY)

    def raise_err():
        raise RuntimeError("tools not initialised")

    monkeypatch.setattr("gptme.tools.get_tools", lambda: raise_err())
    registered = register_skill_commands()

    # Canonical must still be registered (tool-list failure doesn't block it).
    assert "skill:demo" in registered
    # Bare alias must be suppressed: fail-safe means no alias when tool list is unavailable.
    assert "demo" not in registered
    assert "demo" not in _command_registry


def test_is_skill_command_returns_true_for_registered(skills_root: Path):
    """is_skill_command returns True for both canonical and bare alias forms."""
    _write_skill(skills_root, "sentinel", "A sentinel skill", DEMO_BODY)
    register_skill_commands()
    try:
        # Canonical form
        assert is_skill_command("skill:sentinel")
        # Bare alias form (no collision with built-in commands or tools)
        assert is_skill_command("sentinel")
        # Non-registered names return False
        assert not is_skill_command("help")
        assert not is_skill_command("nonexistent")
        assert not is_skill_command("")
    finally:
        unregister_skill_commands()
        clear_cache()
