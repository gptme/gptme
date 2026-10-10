"""Recall tool results dropped from a compacted conversation view."""

from ..logmanager import LogManager
from ..message import Message
from ..util.master_context import is_tool_result_message
from .base import Parameter, ToolFunction, ToolSpec

_DEFAULT_MAX_CHARS = 20_000
_MAX_CHARS = 100_000


def recall_result(
    result_id: int,
    start_char: int = 0,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Read a tool result from the current conversation's lossless master log.

    Args:
        result_id: Stable 1-based result ID shown in a ``[result #N, ...]`` stub.
        start_char: Character offset to start reading from.
        max_chars: Maximum characters to return (default 20,000; max 100,000).

    Returns:
        The requested result chunk and the next offset when more remains.
    """
    if start_char < 0:
        return "Error: start_char must be zero or greater."
    if max_chars <= 0 or max_chars > _MAX_CHARS:
        return f"Error: max_chars must be between 1 and {_MAX_CHARS:,}."

    looked = _lookup_master_result(result_id)
    if isinstance(looked, str):
        return looked

    return _format_chunk(looked, result_id, start_char, max_chars)


def _lookup_master_result(result_id: int) -> Message | str:
    """Return the master-log message for ``result_id`` or an error string."""
    manager = LogManager.get_current_log()
    if manager is None:
        return "Error: no current conversation is available for result recall."

    messages = manager.master_log.messages
    index = result_id - 1
    if index < 0 or index >= len(messages):
        return f"Error: result #{result_id} does not exist in the master log."
    if not is_tool_result_message(messages, index):
        return f"Error: master-log message #{result_id} is not a tool result."
    return messages[index]


def _format_chunk(
    message: Message, result_id: int, start_char: int, max_chars: int
) -> str:
    """Format a paged chunk of a master-log result with header and continuation."""
    content = message.content
    if start_char > len(content):
        return (
            f"Error: start_char {start_char:,} is past the end of result "
            f"#{result_id} ({len(content):,} characters)."
        )

    end_char = min(len(content), start_char + max_chars)
    chunk = content[start_char:end_char]
    header = (
        f"result #{result_id} (characters {start_char:,}-{end_char:,} "
        f"of {len(content):,}):"
    )
    if end_char < len(content):
        continuation = (
            f"\n\n[More available: recall_result({result_id}, start_char={end_char})]"
        )
    else:
        continuation = ""
    return f"{header}\n{chunk}{continuation}"


def execute_recall(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Message:
    """Native tool-call handler: recall a dropped result by stable ID."""
    kwargs = kwargs or {}

    def _int(name: str, position: int, default: int) -> int:
        raw = kwargs.get(name)
        if raw is None and args and len(args) > position:
            raw = args[position]
        try:
            return int(raw) if raw is not None else default
        except ValueError:
            return default

    result_id = _int("result_id", 0, 0)
    start_char = _int("start_char", 1, 0)
    max_chars = _int("max_chars", 2, _DEFAULT_MAX_CHARS)
    if result_id <= 0:
        return Message(
            "system",
            "Error: recall_result requires a result_id from a [result #N, ...] stub.",
        )
    if start_char < 0:
        return Message("system", "Error: start_char must be zero or greater.")
    if max_chars <= 0 or max_chars > _MAX_CHARS:
        return Message(
            "system",
            f"Error: max_chars must be between 1 and {_MAX_CHARS:,}.",
        )

    looked = _lookup_master_result(result_id)
    if isinstance(looked, str):
        return Message("system", looked)

    # Preserve the source message's display flag: a hidden result (e.g. a
    # redacted elicit secret) stays available to the assistant but must not
    # surface in ordinary chat output through the recalled message.
    return Message(
        "system",
        _format_chunk(looked, result_id, start_char, max_chars),
        hide=looked.hide,
    )


instructions = """
Use recall_result() when a compacted conversation contains a
`[result #N, M tokens]` stub and the original tool output is needed. Result IDs
refer only to the current conversation's lossless master log. Large results are
paged; continue from the start_char shown in the response.
""".strip()


tool = ToolSpec(
    name="recall",
    desc="Recall a dropped tool result from the current conversation's lossless master log",
    instructions=instructions,
    instructions_format={
        "tool": "Recall compacted tool outputs by stable result ID with "
        "recall_result(result_id, start_char=0, max_chars=20000)."
    },
    functions=[ToolFunction.from_callable(recall_result)],
    parameters=[
        Parameter(
            "result_id",
            "integer",
            "Stable result ID from a [result #N, ...] stub.",
            required=True,
        ),
        Parameter(
            "start_char",
            "integer",
            "Character offset to start reading (default 0).",
            required=False,
        ),
        Parameter(
            "max_chars",
            "integer",
            "Characters to return (default 20000; max 100000).",
            required=False,
        ),
    ],
    execute=execute_recall,
    read_only=True,
)

__doc__ = tool.get_doc(__doc__)
