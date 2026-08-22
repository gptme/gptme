"""Expose skills as user-invocable slash commands.

Claude Code and Codex expose every ``SKILL.md`` skill as a ``/<name>`` command
that injects the skill body as the user's prompt. This module gives gptme the
same behavior so skills can be shared across runtimes:

- ``/skill:<name> [args]`` is the canonical, collision-free form.
- ``/<name>`` is registered as an alias only when ``<name>`` does not collide
  with an existing command or a loaded tool (``handle_cmd`` falls back to tool
  execution for unknown names, so a skill named ``shell`` must never shadow
  the shell tool).

The handler substitutes ``$ARGUMENTS`` (and ``$ARGUMENTS[N]`` / ``$N``
positional forms) in the skill body and queues the result via
:func:`gptme.prompt_queue.queue_prompt`, so the main chat loop drains it as a
normal user message on its next iteration and the assistant acts on it.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .index import LessonIndex

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..commands.base import CommandContext, CommandHandler
    from ..message import Message
    from .parser import Lesson

logger = logging.getLogger(__name__)

SKILL_COMMAND_PREFIX = "skill:"

# Names registered by the most recent register_skill_commands() call.
# Used to make re-registration idempotent and to let our own previous bare
# aliases not count as collisions.
_registered_skill_commands: dict[str, CommandHandler] = {}

_ARG_PATTERN = re.compile(r"\$ARGUMENTS\[(\d+)\]|\$ARGUMENTS\b|\$(\d+)")


def substitute_arguments(body: str, full_args: str, args: list[str]) -> str:
    """Substitute ``$ARGUMENTS``, ``$ARGUMENTS[N]`` and ``$N`` placeholders.

    ``$ARGUMENTS`` expands to the full argument string; ``$ARGUMENTS[N]`` and
    ``$N`` expand to the N-th (0-based) whitespace-split argument, or the empty
    string when out of range.
    """

    def _replace(match: re.Match[str]) -> str:
        idx_str = match.group(1) or match.group(2)
        if idx_str is None:
            return full_args
        idx = int(idx_str)
        # Return the original text unchanged when the index is out of range,
        # so "$100" in skill prose (prices, currency) is never silently deleted.
        return args[idx] if idx < len(args) else match.group(0)

    return _ARG_PATTERN.sub(_replace, body)


def build_skill_prompt(
    skill: Lesson, index: LessonIndex, full_args: str, args: list[str]
) -> str:
    """Materialize a skill and render it as a user prompt."""
    if skill.is_stub:
        skill = index.materialize_lesson(skill)
    name = skill.metadata.name or skill.path.stem
    header = f"Skill invoked: /{SKILL_COMMAND_PREFIX}{name}"
    if full_args:
        header += f" {full_args}"
    body = substitute_arguments(skill.body, full_args, args)
    return f"{header}\n\n{body}".rstrip()


def _make_skill_handler(skill: Lesson, index: LessonIndex) -> CommandHandler:
    name = skill.metadata.name or skill.path.stem

    def handler(ctx: CommandContext) -> Generator[Message, None, None]:
        from ..prompt_queue import queue_prompt  # fmt: skip

        # Remove the "/skill:<name>" command message from the log, like built-in
        # commands do; the queued prompt carries its own "Skill invoked" header.
        ctx.manager.undo(1, quiet=True)
        ctx.manager.write()

        content = build_skill_prompt(skill, index, ctx.full_args.strip(), ctx.args)
        queue_prompt(ctx.manager.logdir, content)
        logger.debug("Queued skill prompt for /%s%s", SKILL_COMMAND_PREFIX, name)
        # Yield nothing: the chat loop drains the queue on its next iteration
        # and the assistant responds to the skill body as a normal user turn.
        yield from ()

    desc = skill.metadata.description or skill.description or f"Invoke skill {name}"
    handler.__doc__ = desc.strip().split("\n")[0]
    handler.__name__ = f"skill_{name}"
    return handler


def _loaded_tool_names() -> set[str]:
    try:
        from ..tools import get_tools  # fmt: skip

        return {tool.name for tool in get_tools()}
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Could not list loaded tools for skill alias check: %s", e)
        return set()


def register_skill_commands(index: LessonIndex | None = None) -> list[str]:
    """Register every skill in the lesson index as a ``/skill:<name>`` command.

    Also registers a bare ``/<name>`` alias when it collides with neither an
    existing command nor a loaded tool. Safe to call repeatedly (stale skill
    commands from a previous call are removed first) and never raises.

    Returns:
        The list of command names registered.
    """
    from ..commands.base import (  # fmt: skip
        _command_registry,
        register_command,
        unregister_command,
    )

    registered: list[str] = []
    try:
        # Drop commands from a previous registration so renamed/removed skills
        # do not linger and re-registration stays idempotent.
        for old_name, old_handler in list(_registered_skill_commands.items()):
            if _command_registry.get(old_name) is old_handler:
                unregister_command(old_name)
        _registered_skill_commands.clear()

        if index is None:
            index = LessonIndex()
        tool_names = _loaded_tool_names()

        for skill in index.lessons:
            name = skill.metadata.name
            if not name:
                continue
            name = name.strip()
            if not name or any(ch.isspace() for ch in name):
                logger.warning("Skipping skill with invalid name: %r", name)
                continue

            try:
                handler = _make_skill_handler(skill, index)
            except Exception as e:
                logger.warning("Failed to build command for skill %r: %s", name, e)
                continue

            canonical = f"{SKILL_COMMAND_PREFIX}{name}"
            if canonical in _command_registry:
                logger.debug(
                    "Skill canonical %r already registered by another component; skipping",
                    canonical,
                )
                continue
            register_command(canonical, handler)
            _registered_skill_commands[canonical] = handler
            registered.append(canonical)

            if name in _command_registry:
                logger.debug(
                    "Skill %r collides with existing command; only /%s registered",
                    name,
                    canonical,
                )
            elif name in tool_names:
                logger.debug(
                    "Skill %r collides with loaded tool; only /%s registered",
                    name,
                    canonical,
                )
            else:
                register_command(name, handler)
                _registered_skill_commands[name] = handler
                registered.append(name)

        if registered:
            logger.debug("Registered %d skill command(s)", len(registered))
    except Exception as e:
        logger.warning("Failed to register skill commands: %s", e)

    return registered


def unregister_skill_commands() -> None:
    """Remove all commands registered by :func:`register_skill_commands`."""
    from ..commands.base import _command_registry, unregister_command  # fmt: skip

    for name, handler in list(_registered_skill_commands.items()):
        if _command_registry.get(name) is handler:
            unregister_command(name)
    _registered_skill_commands.clear()
