"""
RAG (Retrieval-Augmented Generation) tool for context-aware assistance.

The RAG tool provides context-aware assistance by indexing and semantically searching text files.

.. rubric:: Installation

The RAG tool requires the ``gptme-rag`` CLI to be installed::

    pipx install gptme-rag

.. rubric:: Configuration

Configure RAG in your ``gptme.toml``::

    [rag]
    enabled = true
    post_process = false # Whether to post-process the context with an LLM to extract the most relevant information
    post_process_model = "openai/gpt-4o-mini" # Which model to use for post-processing
    post_process_prompt = "" # Optional prompt to use for post-processing (overrides default prompt)
    workspace_only = true # Whether to only search in the workspace directory, or the whole RAG index
    paths = [] # List of paths to include in the RAG index. Has no effect if workspace_only is true.

.. rubric:: Features

1. Manual Search and Indexing

   - Index project documentation with ``rag_index``
   - Search indexed documents with ``rag_search``
   - Check index status with ``rag_status``

2. Conversation Indexing

   - Index past gptme conversations with ``rag_index_conversations``
   - Only indexes user and assistant messages (skips system prompts)
   - Enables semantic search across your conversation history

3. Automatic Context Enhancement

   - Retrieves semantically similar documents
   - Preserves conversation flow with hidden context messages
"""

import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from ..config import RagConfig, get_project_config
from ..dirs import get_data_dir, get_logs_dir, get_project_gptme_dir
from ..llm import _chat_complete
from ..message import Message
from .base import ToolFunction, ToolSpec, ToolUse

logger = logging.getLogger(__name__)


def _sanitize_project_name(name: str) -> str:
    """Sanitize a project name for use as a filesystem path component."""
    # Replace path separators and whitespace with underscores
    sanitized = re.sub(r"[/\\\s]+", "_", name)
    # Remove any remaining non-alphanumeric/underscore/hyphen/dot characters
    sanitized = re.sub(r"[^\w\-.]", "_", sanitized)
    return sanitized.strip("_") or "default"


def _project_persist_dir(project: str | None) -> Path | None:
    """Return the persist directory for a project, or None to use the global index."""
    if not project:
        return None
    return get_data_dir() / "rag" / _sanitize_project_name(project)


instructions = """
### When to use RAG

Use RAG for semantic search across indexed documents when you do not know the
exact file location or keyword. Prefer `shell` with grep/ripgrep for exact
string or pattern matching. Use `read` when you already know the file path.
Index first with `rag_index`, then search with `rag_search`.
"""


def examples(tool_format):
    return f"""
User: Index the current directory
Assistant: Let me index the current directory with RAG.
{ToolUse("ipython", [], "rag_index()").to_output(tool_format)}
System: Indexed 1 paths

User: Search for documentation about functions
Assistant: I'll search for function-related documentation.
{ToolUse("ipython", [], 'rag_search("function documentation")').to_output(tool_format)}
System: ### docs/api.md
Functions are documented using docstrings...

User: Show index status
Assistant: I'll check the current status of the RAG index.
{ToolUse("ipython", [], "rag_status()").to_output(tool_format)}
System: Index contains 42 documents

User: Index my past conversations so I can search them
Assistant: I'll index your recent conversations with RAG.
{ToolUse("ipython", [], "rag_index_conversations()").to_output(tool_format)}
System: Indexed 47 conversations.
Indexed 47 paths

User: Index only the last 10 conversations
Assistant: I'll index just the 10 most recent conversations.
{ToolUse("ipython", [], "rag_index_conversations(n=10)").to_output(tool_format)}
System: Indexed 10 conversations.
Indexed 10 paths
"""


DEFAULT_POST_PROCESS_PROMPT = """
You are an intelligent knowledge retrieval assistant designed to analyze context chunks and extract relevant information based on user queries. Your primary goal is to provide accurate and helpful information while adhering to specific guidelines.

You will be provided with a user query inside <user_query> tags and a list of potentially relevant context chunks inside <chunks> tags.

When a user submits a query, follow these steps:

1. Analyze the user's query carefully to identify key concepts and requirements.

2. Search through the provided context chunks for relevant information.

3. If you find relevant information:
   a. Extract the most pertinent parts.
   b. Summarize the relevant context inside <context_summary> tags.
   c. Output the exact relevant context chunks, including the complete <chunks path="...">...</chunks> tags.

4. If you cannot find any relevant information, respond with exactly: "No relevant context found".

Important guidelines:
- Do not make assumptions beyond the available data.
- Maintain objectivity in source selection.
- When returning context chunks, include the entire content of the <chunks> tag. Do not modify or truncate it in any way.
- Ensure that you're providing complete information from the chunks, not partial or summarized versions within the tags.
- When no relevant context is found, do not return anything other than exactly "No relevant context found".
- Do not output anything else than the <context_summary> and <chunks> tags.

Please provide your response, starting with the summary and followed by the relevant chunks (if any).
"""


@lru_cache
def _has_gptme_rag() -> bool:
    """Check if gptme-rag is available in PATH."""
    return shutil.which("gptme-rag") is not None


def _run_rag_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a gptme-rag command and handle errors."""
    start = time.monotonic()
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=60
        )
    except subprocess.TimeoutExpired as e:
        logger.error("gptme-rag command timed out after 60s")
        raise RuntimeError("gptme-rag command timed out after 60s") from e
    except subprocess.CalledProcessError as e:
        logger.error(f"gptme-rag command failed: {e.stderr}")
        raise RuntimeError(f"gptme-rag command failed: {e.stderr}") from e
    finally:
        cmd_str = " ".join(cmd)
        logger.info(
            f"Ran RAG: `{cmd_str[:100] if len(cmd_str) > 100 else cmd_str}` in {time.monotonic() - start:.2f}s"
        )


def rag_index(*paths: str, glob: str | None = None, project: str | None = None) -> str:
    """Index documents in specified paths.

    Args:
        paths: Paths to index (files or directories). Defaults to current directory.
        glob: Glob pattern to filter files.
        project: Project name to scope the index to. When set, documents are stored
            in a project-specific index isolated from all other projects and the
            global index. When omitted, uses the global index.
    """
    paths = paths or (".",)
    cmd = ["gptme-rag", "index"]
    cmd.extend(paths)
    if glob:
        cmd.extend(["--glob", glob])
    persist_dir = _project_persist_dir(project)
    if persist_dir is not None:
        cmd.extend(["--persist-dir", str(persist_dir)])

    result = _run_rag_cmd(cmd)
    return result.stdout.strip()


def rag_search(
    query: str,
    return_full: bool = False,
    top_k: int | None = None,
    project: str | None = None,
) -> str:
    """Search indexed documents.

    Args:
        query: Search query.
        return_full: Return full document content instead of excerpts.
        top_k: Maximum number of results to return.
        project: Project name to restrict the search to. Must match the project
            used when indexing. When omitted, searches the global index.
    """
    cmd = ["gptme-rag", "search", query]
    if return_full:
        # shows full context of the search results
        cmd.extend(["--raw"])
    if top_k is not None:
        cmd.extend(["--top-k", str(top_k)])
    persist_dir = _project_persist_dir(project)
    if persist_dir is not None:
        cmd.extend(["--persist-dir", str(persist_dir)])

    result = _run_rag_cmd(cmd)
    return result.stdout.strip()


def rag_status(project: str | None = None) -> str:
    """Show index status.

    Args:
        project: Project name to show status for. When set, shows information
            about the project-specific index directory. When omitted, shows the
            global index status.
    """
    persist_dir = _project_persist_dir(project)
    if persist_dir is not None:
        # gptme-rag status does not support --persist-dir, so we report directory info
        if persist_dir.exists():
            file_count = sum(1 for f in persist_dir.rglob("*") if f.is_file())
            size_bytes = sum(
                f.stat().st_size for f in persist_dir.rglob("*") if f.is_file()
            )
            size_kb = size_bytes / 1024
            return (
                f"Project index '{project}' at {persist_dir}\n"
                f"Storage files: {file_count} ({size_kb:.1f} KB)"
            )
        return (
            f"Project index '{project}' not found at {persist_dir}\n"
            f"Run rag_index(project='{project}') to create it."
        )
    cmd = ["gptme-rag", "status"]
    result = _run_rag_cmd(cmd)
    return result.stdout.strip()


def rag_index_conversations(
    n: int = 100,
    output_dir: str | None = None,
) -> str:
    """Index past gptme conversations for semantic search.

    Exports user and assistant messages from conversation logs (skipping system
    prompts) into text files and indexes them with gptme-rag.

    Args:
        n: Maximum number of recent conversations to index (default: 100).
        output_dir: Directory to write exported conversation files.
            Defaults to a temporary directory managed by gptme-rag.

    Returns:
        Status message from the indexing operation.
    """
    from ..logmanager import _gen_read_jsonl

    if n <= 0:
        raise ValueError(f"n must be a positive integer, got {n}")

    logs_dir = get_logs_dir()
    conv_files = sorted(
        logs_dir.glob("*/conversation.jsonl"),
        key=lambda f: -f.stat().st_mtime,
    )[:n]

    if not conv_files:
        return "No conversations found to index."

    if output_dir:
        export_path = Path(output_dir)
        export_path.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        _tmpdir = tempfile.mkdtemp(prefix="gptme-rag-convs-")
        export_path = Path(_tmpdir)
        cleanup = True

    exported = 0
    try:
        for conv_file in conv_files:
            conv_id = conv_file.parent.name
            msgs = list(_gen_read_jsonl(conv_file))
            # Only include user and assistant messages — system prompts add noise
            content_msgs = [m for m in msgs if m.role in ("user", "assistant")]
            if not content_msgs:
                continue

            lines = []
            for msg in content_msgs:
                role_label = msg.role.capitalize()
                # Handle multimodal content (list) — extract text parts only
                if isinstance(msg.content, list):
                    text = " ".join(
                        part.get("text", "")
                        for part in msg.content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                else:
                    text = msg.content
                if text.strip():
                    lines.append(f"**{role_label}**: {text.strip()}")

            if lines:
                out_file = export_path / f"{conv_id}.md"
                out_file.write_text("\n\n".join(lines), encoding="utf-8")
                exported += 1

        if exported == 0:
            return "No conversation content to index."

        result = _run_rag_cmd(["gptme-rag", "index", str(export_path)])
        return f"Indexed {exported} conversations.\n{result.stdout.strip()}"
    finally:
        if cleanup:
            shutil.rmtree(export_path, ignore_errors=True)


def init() -> ToolSpec:
    """Initialize the RAG tool."""
    # Check if gptme-rag CLI is available
    if not _has_gptme_rag():
        logger.debug("gptme-rag CLI not found in PATH")
        return replace(tool, available=False)

    # Check project configuration
    project_dir = get_project_gptme_dir()
    if project_dir and (config := get_project_config(project_dir)):
        enabled = config.rag.enabled
        if not enabled:
            logger.debug("RAG not enabled in the project configuration")
            return replace(tool, available=False)
    else:
        logger.debug("Project configuration not found, not enabling")
        return replace(tool, available=False)

    return tool


def get_rag_context(
    query: str,
    rag_config: RagConfig,
    workspace: Path | None = None,
) -> Message:
    """Get relevant context chunks from RAG for the user query."""

    should_post_process = (
        rag_config.post_process and rag_config.post_process_model is not None
    )

    cmd = [
        "gptme-rag",
        "search",
        query,
    ]
    if workspace and rag_config.workspace_only:
        cmd.append(workspace.as_posix())
    elif rag_config.paths:
        cmd.extend(rag_config.paths)
    if not should_post_process:
        cmd.append("--score")
    cmd.extend(["--format", "full"])

    if rag_config.max_tokens:
        cmd.extend(["--max-tokens", str(rag_config.max_tokens)])
    if rag_config.min_relevance:
        cmd.extend(["--min-relevance", str(rag_config.min_relevance)])
    rag_result = _run_rag_cmd(cmd).stdout

    # Post-process the context with an LLM (if enabled)
    if should_post_process:
        assert rag_config.post_process_model is not None
        post_process_msgs = [
            Message(
                role="system",
                content=rag_config.post_process_prompt or DEFAULT_POST_PROCESS_PROMPT,
            ),
            Message(role="system", content=rag_result),
            Message(
                role="user",
                content=f"<user_query>\n{query}\n</user_query>",
            ),
        ]
        start = time.monotonic()
        rag_result, _metadata = _chat_complete(
            messages=post_process_msgs,
            model=rag_config.post_process_model,
            tools=[],
        )
        logger.info(f"Ran RAG post-process in {time.monotonic() - start:.2f}s")

    # Create the context message
    msg = Message(
        role="system",
        content=f"Relevant context retrieved using `gptme-rag search`:\n\n{rag_result}",
        hide=True,
    )
    return msg


def _rag_context_hook(
    messages: list[Message],
    **kwargs,
):
    """Hook to add RAG context before generation."""
    if not _has_gptme_rag():
        return

    workspace = kwargs.get("workspace")

    # Load config
    config = get_project_config(Path.cwd())
    rag_config = config.rag if config and config.rag else RagConfig()

    if not rag_config.enabled:
        return

    last_msg = messages[-1] if messages else None
    if last_msg and last_msg.role == "user":
        try:
            # Get context using gptme-rag CLI
            msg = get_rag_context(last_msg.content, rag_config, workspace)
            yield msg
        except Exception as e:
            logger.warning(f"Error getting RAG context: {e}")


tool = ToolSpec(
    name="rag",
    desc="RAG (Retrieval-Augmented Generation) for context-aware assistance",
    instructions=instructions,
    examples=examples,
    functions=[
        ToolFunction.from_callable(f)
        for f in [rag_index, rag_search, rag_status, rag_index_conversations]
    ],
    available=_has_gptme_rag,
    init=init,
    hooks={
        "rag_context": ("generation.pre", _rag_context_hook, 0),
    },
)

__doc__ = tool.get_doc(__doc__)
