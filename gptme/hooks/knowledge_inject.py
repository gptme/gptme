"""Inject matching personal KB entries at session start.

Follow-on to gptme/gptme#3596 / #3622. Save/retrieve already landed; this
hook is the session-start prompt injection slice. Uses the JSONL keyword
search (same as ``gptme-util knowledge search``). gptme-rag-backed search
and schema generalization are separate follow-ons.
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..knowledge import format_knowledge_prompt, select_knowledge_for_session
from ..message import Message

logger = logging.getLogger(__name__)


def _query_from_initial_msgs(initial_msgs: list[Message] | None) -> str | None:
    if not initial_msgs:
        return None
    for msg in reversed(list(initial_msgs)):
        if getattr(msg, "role", None) != "user":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            return content
    return None


def inject_session_knowledge(
    logdir: Path,
    workspace: Path | None,
    initial_msgs: list[Message] | None = None,
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Yield a hidden system message with matching personal KB entries.

    No-ops when the store is empty, the initial prompt is too short to
    search, or nothing matches. Failures never block session start.
    """
    try:
        query = _query_from_initial_msgs(initial_msgs)
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
