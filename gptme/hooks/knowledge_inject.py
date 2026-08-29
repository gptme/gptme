"""Inject matching personal KB entries at session start.

Follow-on to gptme/gptme#3596 / #3622. Save/retrieve already landed; this
hook is the session-start prompt injection slice. Uses the JSONL keyword
search (same as ``gptme-util knowledge search``). gptme-rag-backed search
and schema generalization are separate follow-ons.

CLI ``SESSION_START`` runs before the first user prompt is appended, so
this hook also registers on ``TURN_PRE`` (after the prompt is in the log).
ACP and TUI fire ``TURN_PRE`` on that same boundary. Injection is once per
conversation: later turns no-op if ``<knowledge-entries>`` is already present.
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..knowledge import format_knowledge_prompt, select_knowledge_for_session
from ..message import Message

logger = logging.getLogger(__name__)

_KNOWLEDGE_MARK = "<knowledge-entries>"


def _messages_from_context(
    initial_msgs: list[Message] | None,
    manager: Any,
) -> list[Message]:
    if manager is not None:
        log = getattr(manager, "log", manager)
        msgs = getattr(log, "messages", None)
        if msgs:
            return list(msgs)
    return list(initial_msgs or [])


def _query_from_msgs(msgs: list[Message]) -> str | None:
    for msg in reversed(msgs):
        if getattr(msg, "role", None) != "user":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            return content
    return None


def _already_injected(msgs: list[Message]) -> bool:
    return any(
        isinstance(getattr(msg, "content", None), str)
        and _KNOWLEDGE_MARK in msg.content
        for msg in msgs
    )


def inject_session_knowledge(
    logdir: Path | None = None,
    workspace: Path | None = None,
    initial_msgs: list[Message] | None = None,
    manager: Any = None,
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Yield a hidden system message with matching personal KB entries.

    No-ops when the store is empty, the initial prompt is too short to
    search, nothing matches, or this conversation already received a
    knowledge block. Failures never block session start.
    """
    try:
        if manager is not None:
            logdir = logdir or getattr(manager, "logdir", None)
            if workspace is None:
                workspace = getattr(manager, "workspace", None)
        msgs = _messages_from_context(initial_msgs, manager)
        if _already_injected(msgs):
            return
        query = _query_from_msgs(msgs)
        entries = select_knowledge_for_session(query)
        if not entries:
            return
        body = format_knowledge_prompt(entries)
        if not body.strip():
            return
        logger.debug(
            "Injecting %d knowledge entries for session %s (workspace=%s)",
            len(entries),
            logdir,
            workspace,
        )
        yield Message("system", body, hide=True)
    except Exception:
        logger.debug("knowledge session inject skipped", exc_info=True)
        return


def register() -> None:
    register_hook(
        "knowledge_inject.session_start",
        HookType.SESSION_START,
        inject_session_knowledge,
        priority=0,
    )
    # CLI SESSION_START does not include prompt_msgs; TURN_PRE does.
    register_hook(
        "knowledge_inject.turn_pre",
        HookType.TURN_PRE,
        inject_session_knowledge,
        priority=0,
    )
