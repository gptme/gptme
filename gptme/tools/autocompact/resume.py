"""LLM-powered conversation summarization (resume generation).

Creates structured summaries of conversations using LLM, extracts
context files, and manages conversation resumption.
"""

import logging
import re
from collections.abc import Generator
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from ... import llm
from ...llm.models import get_default_model
from ...logmanager import Log, prepare_messages
from ...message import Message, len_tokens
from ...tools import ToolUse
from ...util.context import md_codeblock
from ...util.context_budget import get_context_budget

if TYPE_CHECKING:
    from ...logmanager import LogManager

# Default keep_recent window — last N tokens of history kept verbatim after checkpoint
_DEFAULT_KEEP_RECENT_TOKENS = 20_000

# Reserved output budget for the structured checkpoint call (1.5b). Until the
# model-participating checkpoint turn lands, the checkpoint prompt is capped
# here and its input is clipped to ``context_window - _CHECKPOINT_MAX_OUTPUT``
# so the request cannot overflow the model window.
_CHECKPOINT_MAX_OUTPUT = 8192

logger = logging.getLogger(__name__)


def _parse_context_files(content: str) -> list[str]:
    """
    Parse file paths from the LLM-generated resume.

    Looks for a "Context Files" or "Files to Include" section and extracts
    file paths that start with / or ./ or are relative paths.

    Args:
        content: The LLM-generated resume content

    Returns:
        List of file paths found in the response
    """
    file_paths: list[str] = []

    # Find the Context Files section (case-insensitive)
    # Look for patterns like "## Context Files" or "### Files to Include"
    context_section_pattern = r"(?:#{1,4}\s*(?:Context Files|Files to Include|Recommended Files|Key Files)[^\n]*\n)([\s\S]*?)(?=\n#{1,4}\s|\Z)"
    match = re.search(context_section_pattern, content, re.IGNORECASE)

    if match:
        section_content = match.group(1)
    else:
        # If no explicit section, scan the whole content
        section_content = content

    # Extract file paths from markdown list items
    # Matches: - `/path/to/file.py` or - `./relative/path.md` or - path/to/file.txt
    # Also matches: - `/path/to/file.py` - description
    path_patterns = [
        # Backtick-wrapped paths: - `path/to/file`
        r"[-*]\s*`([^`]+)`",
        # Paths starting with / or ./ or ~/
        r"[-*]\s*([/~.][\w./\-]+(?:\.\w+)?)",
        # Relative paths without leading dot (common patterns)
        r"[-*]\s*((?:src|docs|tests|config|scripts|tasks|journal|knowledge|lessons)/[\w./\-]+(?:\.\w+)?)",
    ]

    for pattern in path_patterns:
        matches = re.findall(pattern, section_content)
        for m in matches:
            path = m.strip()
            # Filter out common false positives
            if path and not path.startswith("http") and not path.startswith("#"):
                # Normalize path
                path = path.removeprefix("./")
                file_paths.append(path)

    # Remove duplicates while preserving order
    seen = set()
    unique_paths = []
    for p in file_paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    return unique_paths


def _load_context_files(
    file_paths: list[str],
    workspace: Path | None = None,
    max_tokens_per_file: int = 2000,
) -> list[tuple[str, str]]:
    """
    Load contents of specified files that exist inside the workspace.

    Absolute and ``~/`` paths are resolved, then rejected unless they stay
    inside ``workspace``. This is load-bearing now that autocompact's
    LLM-powered summarize branch can run without an explicit tool allowlist:
    untrusted conversation content must not be able to name ``~/.ssh/id_rsa``
    and have its contents inserted into the compacted log.

    Args:
        file_paths: List of file paths to load
        workspace: Workspace directory for resolving relative paths
        max_tokens_per_file: Maximum tokens to include per file

    Returns:
        List of (path, content) tuples for files that exist and are readable
    """
    loaded_files: list[tuple[str, str]] = []
    workspace_path = (workspace or Path.cwd()).resolve()

    for file_path in file_paths:
        # Resolve path. Absolute and ~/ suggestions are allowed only when the
        # resolved file stays inside the workspace — the summarizer output is
        # model-generated and must not become a local-file exfil path.
        if file_path.startswith("~"):
            candidate = Path(file_path).expanduser()
        elif file_path.startswith("/"):
            candidate = Path(file_path)
        else:
            candidate = workspace_path / file_path

        try:
            full_path = candidate.resolve()
        except (OSError, RuntimeError) as e:
            logger.warning(f"Could not resolve context file {file_path}: {e}")
            continue

        if not full_path.is_relative_to(workspace_path):
            logger.warning(
                "Skipping context file outside workspace: %s (resolved to %s)",
                file_path,
                full_path,
            )
            continue

        try:
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8", errors="replace")

                # Truncate if too long
                tokens = len_tokens(content, "gpt-4")
                if tokens > max_tokens_per_file:
                    # Simple truncation with note
                    lines = content.split("\n")
                    truncated_lines = []
                    current_tokens = 0
                    for line in lines:
                        line_tokens = len_tokens(line, "gpt-4")
                        if current_tokens + line_tokens > max_tokens_per_file - 50:
                            truncated_lines.append(
                                f"\n... (truncated, {tokens - current_tokens} tokens remaining)"
                            )
                            break
                        truncated_lines.append(line)
                        current_tokens += line_tokens
                    content = "\n".join(truncated_lines)

                loaded_files.append((str(file_path), content))
                logger.info(
                    f"Loaded context file: {file_path} ({len_tokens(content, 'gpt-4')} tokens)"
                )
        except Exception as e:
            logger.warning(f"Could not load context file {file_path}: {e}")

    return loaded_files


def _logfile_snapshot(path: Path) -> tuple[int, int] | None:
    """Size + mtime of the conversation log, for detecting concurrent appends."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _get_recent_tail(
    msgs: list[Message],
    keep_tokens: int,
    *,
    model: str | None = None,
) -> list[Message]:
    """Return the last messages that fit within keep_tokens, preserving tool-call pairs."""
    if keep_tokens <= 0 or not msgs:
        return []
    tail: list[Message] = []
    total = 0
    model_str: str = model or "gpt-4"
    for msg in reversed(msgs):
        t = len_tokens([msg], model=model_str)
        if total + t > keep_tokens:
            break
        tail.insert(0, msg)
        total += t
    # Drop dangling tool-result at head (no matching tool-call).
    # Tool results can have role="tool" OR a non-tool role with call_id set
    # (e.g. system/user role in some provider formats).
    while tail and (tail[0].role == "tool" or tail[0].call_id):
        tail = tail[1:]
    # Drop a trailing assistant tool-call whose result is not in the tail
    # (e.g. the conversation ends mid-turn). An unmatched tool call at the
    # end of the compacted view breaks strict providers.
    while (
        tail
        and tail[-1].role == "assistant"
        and any(
            tooluse.is_runnable
            for tooluse in ToolUse.iter_from_content(tail[-1].content)
        )
    ):
        tail = tail[:-1]
    return tail


_TRUNCATION_MARK = "\n\n[... middle truncated to fit context budget ...]\n\n"


def _fit_prefix(text: str, max_tokens: int, model_str: str) -> str:
    """Longest fitting prefix, cut at a line boundary when possible."""
    # Estimate then adjust (linear per-char is too slow for large checkpoints).
    ratio = max_tokens / max(1, len_tokens(text, model=model_str))
    candidate = text[: int(len(text) * ratio)]
    while candidate and len_tokens(candidate, model=model_str) > max_tokens:
        candidate = candidate[: int(len(candidate) * 0.9)]
    # Avoid cutting mid-line when possible
    last_nl = candidate.rfind("\n")
    if last_nl > 0:
        candidate = candidate[:last_nl]
    return candidate


def _fit_suffix(text: str, max_tokens: int, model_str: str) -> str:
    """Longest fitting suffix, cut at a line boundary when possible."""
    ratio = max_tokens / max(1, len_tokens(text, model=model_str))
    cut = len(text) - int(len(text) * ratio)
    candidate = text[cut:]
    while candidate and len_tokens(candidate, model=model_str) > max_tokens:
        candidate = candidate[max(1, int(len(candidate) * 0.1)) :]
    first_nl = candidate.find("\n")
    if 0 <= first_nl < len(candidate) - 1:
        candidate = candidate[first_nl + 1 :]
    return candidate


def _truncate_to_tokens(text: str, max_tokens: int, *, model: str | None = None) -> str:
    """Truncate text to approximately max_tokens, keeping head and tail.

    The tail (up to ~30% of the budget) is preserved so trailing checkpoint
    sections such as "Open Items" and "Context Files" survive truncation
    instead of being cut off when the beginning alone is kept.
    """
    if max_tokens <= 0:
        return ""
    model_str = model or "gpt-4"
    total = len_tokens(text, model=model_str)
    if total <= max_tokens:
        return text

    tail_budget = int(max_tokens * 0.3)
    # Only bother preserving a tail when the middle is meaningfully large;
    # a barely-over budget is fine with a plain prefix cut.
    if tail_budget <= 0 or total < max_tokens + 2 * tail_budget:
        return _fit_prefix(text, max_tokens, model_str)

    tail = _fit_suffix(text, tail_budget, model_str)
    tail_tokens = len_tokens(tail, model=model_str) if tail else 0
    head_budget = (
        max_tokens - tail_tokens - len_tokens(_TRUNCATION_MARK, model=model_str)
    )
    if head_budget <= 0:
        return tail
    head = _fit_prefix(text, head_budget, model_str)
    return head + _TRUNCATION_MARK + tail


def _clip_checkpoint_input(
    llm_msgs: list[Message],
    *,
    model_str: str,
    budget: int,
) -> list[Message]:
    """Clip a checkpoint prompt input to a token budget (1.5b).

    Keeps the leading system messages (core prompt, tool instructions) and the
    trailing instruction message, and fills the remaining room with the most
    recent conversation messages — dropping the oldest non-system history when
    the log overshoots the budget. Typically a no-op: auto-compact fires near
    the budget, so the input already fits; this guards against a burst of huge
    tool results pushing the request past the window.

    The first message is always preserved as a system message (truncated if it
    does not fit): strict providers such as Anthropic reject a request whose
    first message is not ``system``, so dropping the head entirely would make
    the checkpoint call fail on exactly the oversized conversations it exists
    to handle.
    """
    if budget <= 0 or len_tokens(llm_msgs, model=model_str) <= budget:
        return llm_msgs

    instruction = llm_msgs[-1]
    # Reserve the instruction, then keep the leading system messages while they
    # fit. Trimming the head too (not just the middle) keeps the result inside
    # the budget even when the system block alone is oversized.
    head: list[Message] = []
    head_room = budget - len_tokens([instruction], model=model_str)
    for msg in llm_msgs[:-1]:
        if msg.role != "system":
            break
        msg_tokens = len_tokens([msg], model=model_str)
        if msg_tokens > head_room:
            break
        head.append(msg)
        head_room -= msg_tokens

    # Never drop the leading system message entirely: Anthropic's
    # ``_transform_system_messages`` raises ``ValueError`` when the first
    # message is not a system message. When the system block is too large to
    # fit alongside the instruction, keep a truncated copy of the first system
    # message so the request stays well-formed.
    if not head and llm_msgs and llm_msgs[0].role == "system":
        first = llm_msgs[0]
        if head_room > 0:
            clipped_first = _truncate_to_tokens(
                first.content, head_room, model=model_str
            )
        else:
            # Degenerate: the instruction alone fills the budget. Keep a short
            # placeholder so the request still leads with a system message.
            clipped_first = "No system prompt fit the remaining context budget."
        head = [first.replace(content=clipped_first)]
        head_room = 0

    # Fill the remaining room with the most recent history. Reuse
    # ``_get_recent_tail`` so tool-call/result pairs stay valid (a dangling
    # tool result or trailing tool call breaks strict providers).
    middle = llm_msgs[len(head) : -1]
    kept = _get_recent_tail(middle, max(0, head_room), model=model_str)
    return head + kept + [instruction]


def _resume_via_llm(
    manager: "LogManager",
    msgs: list[Message],
    use_view_branch: bool = False,
    llm_unlocked: AbstractContextManager[object] | None = None,
    compact_instructions: str | None = None,
    keep_recent_tokens: int = _DEFAULT_KEEP_RECENT_TOKENS,
    keep_head: int = 0,
) -> Generator[Message, None, bool]:
    """Core LLM-powered resume logic: summarize conversation and replace history.

    Returns True iff the compacted view/log was applied. Early exits (too few
    messages, no model, stale discard) return False so callers can skip
    cooldown and side effects that assume a successful compaction.

    Args:
        manager: LogManager that owns the conversation.
        msgs: Messages to summarize.
        use_view_branch: If True, create a view branch (for auto-triggered resume)
            and mark status messages as hidden. If False, replace the log directly
            (for user-invoked /compact resume).
        compact_instructions: Additional instructions to append to the checkpoint prompt
            (from project config or /compact <instructions>).
        keep_recent_tokens: Tokens of recent history to keep verbatim after the
            checkpoint (Phase 2 keep_recent window). Default 20k.
        keep_head: Number of leading messages to preserve verbatim in the new
            view, mirroring the trim path's positional protection. The leading
            system block is always kept; this only extends past it.
    """

    # Prepare messages for summarization
    prepared_msgs = prepare_messages(msgs)

    if len(prepared_msgs) < 3:
        yield Message(
            "system",
            "Not enough conversation history to create a meaningful resume.",
            hide=use_view_branch,
            ui_only=True,
        )
        return False

    # Generate conversation summary using LLM
    yield Message(
        "system",
        "🔄 Generating conversation resume with LLM...",
        hide=use_view_branch,
        ui_only=True,
    )

    resume_prompt = """Context budget has been reached — produce a structured checkpoint before history is compacted.

Write a concise checkpoint with these sections:

## Objective
One sentence: what is this conversation trying to accomplish?

## Key Decisions
Bullet list of important decisions or constraints already established.

## Current State
What has been completed; what is in progress; what blockers exist.

## Open Items
Numbered list of remaining work, in priority order.

## Context Files
Files that must be reloaded to continue effectively. Format:
- `path/to/file.py` — reason this file is needed
- `docs/spec.md` — contains the specification being implemented

Focus on files that are actively referenced or modified. Omit files that are
only mentioned in passing.
"""
    if compact_instructions:
        resume_prompt += f"\n\nAdditional instructions:\n{compact_instructions}"

    # Create a temporary message for the LLM prompt
    resume_request = Message("user", resume_prompt)
    # Use full prepared messages for prompt caching friendliness
    llm_msgs = prepared_msgs + [resume_request]

    # Generate the resume using LLM
    m = get_default_model()
    if not m:
        yield Message(
            "system",
            "❌ Failed to generate resume: No default model configured. "
            "Set OPENAI_API_KEY or ANTHROPIC_API_KEY environment variable.",
            hide=use_view_branch,
            ui_only=True,
        )
        return False

    # Phase 2 (1.5b): clip the checkpoint input to the window minus the reserved
    # output budget so a burst of oversized tool results cannot overflow the
    # summarizer request. Normally a no-op at the compaction trigger point.
    if isinstance(m.context, int) and m.context > 0:
        llm_msgs = _clip_checkpoint_input(
            llm_msgs,
            model_str=m.model,
            budget=max(1, m.context - _CHECKPOINT_MAX_OUTPUT),
        )

    # Phase 2 (1.5b): cap the checkpoint output so a runaway summary cannot
    # consume the whole window (until the model-participating turn lands).
    checkpoint_max_tokens = (
        min(m.max_output, _CHECKPOINT_MAX_OUTPUT)
        if isinstance(m.max_output, int) and m.max_output > 0
        else _CHECKPOINT_MAX_OUTPUT
    )

    snapshot = None
    file_snapshot = None
    conv_snapshot = None
    if llm_unlocked is not None:
        # The conversation lock is released while the summary generates, so
        # other workers may append via their own LogManager instances. Those
        # writes update the file on disk but not this in-memory log, so the
        # in-memory comparison alone can never detect them.
        snapshot = (
            manager.current_view,
            len(manager.log.messages),
            manager.log.messages[-1].content if manager.log.messages else None,
        )
        file_snapshot = _logfile_snapshot(manager.logfile)
        if manager.current_branch != "main" and manager.logdir:
            # On non-main branches, logfile is branches/<branch>.jsonl but
            # concurrent view-path appends (dual-write) update conversation.jsonl
            # and views/<view>.jsonl, not the branch file.  Snapshot
            # conversation.jsonl too so those appends are detected.
            conv_snapshot = _logfile_snapshot(manager.logdir / "conversation.jsonl")
    with llm_unlocked or nullcontext():
        resume_response = llm.reply(
            llm_msgs,
            model=m.full,
            tools=[],
            workspace=None,
            max_tokens=checkpoint_max_tokens,
        )
    if snapshot is not None:
        current = (
            manager.current_view,
            len(manager.log.messages),
            manager.log.messages[-1].content if manager.log.messages else None,
        )
        conv_changed = conv_snapshot is not None and (
            _logfile_snapshot(manager.logdir / "conversation.jsonl") != conv_snapshot
        )
        if (
            current != snapshot
            or _logfile_snapshot(manager.logfile) != file_snapshot
            or conv_changed
        ):
            logger.info(
                "Discarding stale summarizer result; conversation changed during llm.reply"
            )
            yield Message(
                "system",
                "Skipped stale auto-summarize: the conversation changed while "
                "the summary was generating.",
                hide=use_view_branch,
                ui_only=True,
            )
            return False
    resume_content = resume_response.content

    # Save RESUME.md to logdir (not workspace) for reference/debugging
    resume_path: Path | None = None
    if manager.logdir:
        resume_path = manager.logdir / "RESUME.md"
        try:
            with open(resume_path, "w") as f:
                f.write(resume_content)
            logger.info(f"Saved resume to {resume_path}")
        except Exception as e:
            logger.warning(f"Failed to save resume file: {e}")
            resume_path = None

    # Parse and load context files suggested by the LLM
    suggested_files = _parse_context_files(resume_content)
    workspace = manager.workspace
    loaded_files = _load_context_files(suggested_files, workspace=workspace)

    # Extract original system messages (before any user/assistant messages)
    # These contain essential context: core prompt, tool instructions, workspace info
    original_system_msgs = []
    for msg in msgs:
        if msg.role == "system":
            original_system_msgs.append(msg)
        elif msg.role in ("user", "assistant"):
            # Stop when we hit the first non-system message
            break

    # Phase 2 (1.5b): preserve the configured keep_head prefix verbatim in the
    # new view, mirroring the trim path's positional protection. The leading
    # system block is always kept, so this only extends past it.
    head_end = max(len(original_system_msgs), min(keep_head, len(msgs)))
    preserved_head = msgs[:head_end]

    # Create file context messages for each loaded file
    file_context_msgs = []
    for file_path, file_content in loaded_files:
        file_msg = Message(
            "system",
            f"Context file `{file_path}`:\n{md_codeblock('', file_content)}",
        )
        file_context_msgs.append(file_msg)

    # Create the resume intro message
    files_note = ""
    if loaded_files:
        files_note = f" (with {len(loaded_files)} context files)"
    resume_source = str(resume_path) if resume_path else "LLM-generated summary"
    resume_intro_msg = Message(
        "system", f"Previous conversation resumed from {resume_source}{files_note}:"
    )
    resume_msg = Message("assistant", resume_content)

    # Phase 2: keep_recent — include the last N tokens of actual conversation
    # verbatim after the checkpoint so the model has immediate context.
    # One model lookup for both the tail tokenization and the budget guard so
    # they always use the same tokenizer and context window.
    # The leading system messages are re-added verbatim in fixed_parts, so the
    # tail is derived from the conversation after them to avoid duplication.
    model_meta = get_default_model()
    tail_source = msgs[head_end:]
    recent_tail = _get_recent_tail(
        tail_source,
        keep_recent_tokens,
        model=model_meta.model if model_meta else None,
    )

    # Budget guard: if fixed parts + recent_tail exceeds the model's context
    # budget, re-derive the tail within the remaining room so the compacted
    # view actually fits.
    fixed_parts = preserved_head + file_context_msgs + [resume_intro_msg, resume_msg]
    if model_meta and isinstance(model_meta.context, int) and model_meta.context > 0:
        budget = get_context_budget(
            model_meta.context, max_output=model_meta.max_output or 8192
        )
        model_str = model_meta.model

        # keep_head is positional, not budget-aware; if the extended prefix
        # alone would exceed the budget, fall back to the essential system
        # block so the view is never over budget by construction.
        if len_tokens(preserved_head, model=model_str) > budget:
            preserved_head = original_system_msgs

        # If the fixed content alone exceeds the budget, shrink it: drop
        # loaded context files (least essential) newest-last first. The
        # system messages and the checkpoint are kept.
        fixed_tokens = len_tokens(fixed_parts, model=model_str)
        if fixed_tokens > budget:
            essential = preserved_head + [resume_intro_msg, resume_msg]
            essential_tokens = len_tokens(essential, model=model_str)
            # Drop file context messages (least essential) until the whole
            # fixed set fits within the budget.
            while file_context_msgs and (
                essential_tokens
                + sum(len_tokens([m], model=model_str) for m in file_context_msgs)
                > budget
            ):
                file_context_msgs.pop()
            if essential_tokens > budget:
                # Even system messages + checkpoint alone are too large:
                # truncate the checkpoint content to fit, keeping a notice.
                TRUNCATION_NOTICE = "\n\n[checkpoint truncated to fit context budget]"
                overhead = len_tokens(
                    preserved_head + [resume_intro_msg], model=model_str
                ) + len_tokens(TRUNCATION_NOTICE, model=model_str)
                room = max(0, budget - overhead)
                resume_content_trunc = _truncate_to_tokens(
                    resume_content, room, model=model_str
                )
                resume_msg = Message(
                    "assistant",
                    resume_content_trunc + TRUNCATION_NOTICE,
                )
                logger.warning(
                    "Checkpoint + system messages exceed context budget "
                    f"({essential_tokens} > {budget}); checkpoint truncated."
                )
            else:
                dropped_count = len(loaded_files) - len(file_context_msgs)
                logger.warning(
                    "Context files exceed remaining budget; dropped "
                    f"{dropped_count} of the loaded context files to fit."
                )
            fixed_parts = (
                preserved_head + file_context_msgs + [resume_intro_msg, resume_msg]
            )
            fixed_tokens = len_tokens(fixed_parts, model=model_str)

        available = budget - fixed_tokens
        if recent_tail and available < len_tokens(recent_tail, model=model_str):
            recent_tail = _get_recent_tail(
                tail_source, max(0, available), model=model_str
            )

    new_log = fixed_parts + recent_tail

    if use_view_branch:
        view_name = manager.get_next_view_name()
        manager.create_view(view_name, new_log)
        manager.switch_view(view_name)
    else:
        # Replace the log directly (user-invoked /compact resume)
        manager.log = Log(new_log)
        manager.write()

    # Build status message
    files_loaded_str = ""
    if loaded_files:
        files_loaded_str = f"• Context files loaded: {len(loaded_files)}\n"
        for fp, _ in loaded_files[:5]:  # Show first 5
            files_loaded_str += f"  - {fp}\n"
        if len(loaded_files) > 5:
            files_loaded_str += f"  ... and {len(loaded_files) - 5} more\n"
    elif suggested_files:
        files_loaded_str = f"• Suggested files not found: {len(suggested_files)}\n"

    view_note = ""
    if use_view_branch:
        view_note = f"• View: {view_name} (master branch preserved with full history)\n"

    resume_note = ""
    if resume_path:
        resume_note = f"• Resume saved to: {resume_path.absolute()}\n"

    yield Message(
        "system",
        f"✅ LLM-powered resume completed:\n"
        f"• Original conversation ({len(prepared_msgs)} messages) compressed to resume\n"
        f"{resume_note}"
        f"{files_loaded_str}"
        f"{view_note}"
        f"• Conversation history replaced with resume",
        hide=use_view_branch,
        ui_only=True,
    )
    return True
