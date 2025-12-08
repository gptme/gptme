import errno
import json
import logging
import os
import re
import shutil
import subprocess
import urllib
import urllib.parse
from collections import Counter
from copy import copy
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..config import get_config
from ..message import Message
from ..tools import has_tool
from ..tools.browser import read_url
from .gh import (
    get_github_issue_content,
    get_github_pr_content,
    parse_github_url,
    transform_github_url,
)

logger = logging.getLogger(__name__)


def use_fresh_context() -> bool:
    """Check if fresh context mode is enabled.

    Fresh context mode ensures that file contents shown in the context
    are always up to date by:
    - Adding a context message before each user message
    - Including current git status
    - Including contents of recently modified files
    - Marking outdated file contents in the conversation history

    Configuration:
        [context]
        enabled = true  # Opt-in (default: false)

    Backward compatibility:
        - Checks GPTME_FRESH env var if [context] config not available
        - Defaults to False (opt-in) if neither is set
    """
    config = get_config()

    # Check new unified config first
    if config.project and config.project.context:
        return config.project.context.enabled

    # Backward compatibility: check GPTME_FRESH env var
    flag: str | None = config.get_env("GPTME_FRESH")
    if flag is not None:
        return flag.lower() in ("1", "true", "yes")

    # Default: opt-in (false)
    return False


def file_to_display_path(f: Path, workspace: Path | None = None) -> Path:
    """
    Determine how to display the path:

    - If file and pwd is in workspace, show path relative to pwd
    - Otherwise, show absolute path
    """
    cwd = Path.cwd()
    if workspace and workspace in f.parents and workspace in [cwd, *cwd.parents]:
        # NOTE: walk_up only available in Python 3.12+
        try:
            return f.relative_to(cwd)
        except ValueError:
            # If relative_to fails, try to find a common parent
            for parent in cwd.parents:
                try:
                    if workspace in parent.parents or workspace == parent:
                        return f.relative_to(parent)
                except ValueError:
                    continue
            return f.absolute()
    elif Path.home() in f.parents:
        return Path("~") / f.relative_to(os.path.expanduser("~"))
    return f


def md_codeblock(lang: str | Path, content: str) -> str:
    """Wrap content in a markdown codeblock."""
    # we use quadruple backticks to avoid conflicts with triple backticks in the content
    return f"````{lang}\n{content}\n````"


def textfile_as_codeblock(path: Path) -> str | None:
    """Include file content as a codeblock."""
    try:
        if path.exists() and path.is_file():
            try:
                return md_codeblock(path, path.read_text())
            except UnicodeDecodeError:
                return None
    except OSError:
        return None
    return None


def embed_attached_file_content(
    msg: Message, workspace: Path | None = None, check_modified=False
) -> Message:
    """Embed attached file contents inline in a message.

    If the message has file_hashes, attempts to read from content-addressed
    storage first (preserving the file version at message creation time).
    Falls back to the original file path if stored content is not available.
    """
    from ..logmanager import LogManager
    from .file_storage import read_stored_content

    # Keep original paths for hash lookup, transform for display
    files_with_originals = [
        (orig_f, file_to_display_path(orig_f, workspace).expanduser())
        for orig_f in msg.files
    ]
    files_text = {}

    # Get logdir for content-addressed storage lookup
    manager = LogManager.get_current_log()
    logdir = manager.logdir if manager else None

    for orig_f, f in files_with_originals:
        # Try to read from content-addressed storage first (if available)
        stored_content = None
        if logdir and msg.file_hashes:
            # Use original path for hash lookup (matches how files were stored)
            file_hash = msg.file_hashes.get(str(orig_f))
            if file_hash:
                stored_content = read_stored_content(logdir, file_hash, f.suffix)

        if stored_content is not None:
            # Use stored content (preserves original version)
            files_text[f] = md_codeblock(f, stored_content)
        else:
            # Fall back to reading from original path
            try:
                stat = f.stat()
            except FileNotFoundError:
                stat = None
            if not check_modified or (
                stat and stat.st_mtime <= datetime.timestamp(msg.timestamp)
            ):
                content = textfile_as_codeblock(f)
                if not content:
                    # Non-text file, skip
                    continue
                files_text[f] = content
            else:
                if not stat:
                    files_text[f] = md_codeblock(
                        f, "<file not found, may have been moved>"
                    )
                else:
                    files_text[f] = md_codeblock(f, "<file was modified after message>")
    # Get list of display paths for the return value
    display_files = [f for _, f in files_with_originals]
    return replace(
        msg,
        content=msg.content + "\n\n".join(files_text.values()),
        files=[f for f in display_files if f not in files_text],
    )


def git_branch() -> str | None:
    """Get the current git branch name."""
    if shutil.which("git"):
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            if branch.returncode == 0:
                return branch.stdout.strip()
        except subprocess.CalledProcessError:
            logger.error("Failed to get git branch")
            return None
    return None


def gh_pr_status() -> str | None:
    """Get GitHub PR status if available."""
    branch = git_branch()
    if shutil.which("gh") and branch and branch not in ["main", "master"]:
        logger.info(f"Getting PR status for branch: {branch}")
        try:
            p = subprocess.run(
                ["gh", "pr", "view", "--json", "number,title,url,body,comments"],
                capture_output=True,
                text=True,
                check=True,
            )
            p_diff = subprocess.run(
                ["gh", "pr", "diff"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get PR info: {e}")
            return None

        pr = json.loads(p.stdout)
        return f"""Pull Request #{pr["number"]}: {pr["title"]} ({branch})
{pr["url"]}

<body>
{pr["body"]}
</body>

<comments>
{pr["comments"]}
</comments>

<diff>
{p_diff.stdout}
</diff>
"""

    return None


def git_status() -> str | None:
    """Get git status if in a repository."""
    try:
        git_status = subprocess.run(
            ["git", "status", "-vv"], capture_output=True, text=True, check=True
        )
        if git_status.returncode == 0:
            logger.debug("Including git status in context")
            return md_codeblock("git status -vv", git_status.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.debug("Not in a git repository or git not available")
    return None


def get_mentioned_files(msgs: list[Message], workspace: Path | None) -> list[Path]:
    """Count files mentioned in messages."""
    workspace_abs = workspace.resolve() if workspace else None
    files: Counter[Path] = Counter()
    for msg in msgs:
        for f in msg.files:
            # If path is relative and we have a workspace, make it absolute relative to workspace
            if workspace_abs and not f.is_absolute():
                f = (workspace_abs / f).resolve()
            else:
                f = f.resolve()
            files[f] += 1

    if files:
        logger.info(f"Files mentioned: {dict(files)}")

    def file_score(f: Path) -> tuple[int, float]:
        # Sort by mentions and recency
        try:
            mtime = f.stat().st_mtime
            return (files[f], mtime)
        except FileNotFoundError:
            return (files[f], 0)

    return sorted(files.keys(), key=file_score, reverse=True)


# gather_fresh_context removal: Logic moved to gptme.hooks.context


def get_changed_files() -> list[Path]:
    """Returns a list of changed files based on git diff."""
    try:
        p = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [Path(f) for f in p.stdout.splitlines()]
    except subprocess.CalledProcessError as e:
        logger.debug(f"Error getting git diff files: {e}")
        return []


def enrich_messages_with_context(
    msgs: list[Message], workspace: Path | None = None
) -> list[Message]:
    """
    Enrich messages with context by embedding attached file contents.

    Note: Fresh context (git status, modified files) and RAG enhancement are now
    added at generation time via GENERATION_PRE hooks, not here.
    """
    # Make a copy of messages to avoid modifying the original
    msgs = copy(msgs)

    # Embed attached file contents inline
    msgs = [
        embed_attached_file_content(msg, workspace, check_modified=use_fresh_context())
        for msg in msgs
    ]

    return msgs


def include_paths(msg: Message, workspace: Path | None = None) -> Message:
    """
    Searches the message for any valid paths and:
     - In legacy mode (default):
       - includes the contents of text files as codeblocks
       - includes images as msg.files
     - In fresh context mode (GPTME_FRESH=1):
       - breaks the append-only nature of the log, but ensures we include fresh file contents
       - includes all files in msg.files
       - contents are applied right before sending to LLM (only paths stored in the log)

    Args:
        msg: Message to process
        workspace: If provided, paths will be stored relative to this directory
    """
    # TODO: add support for directories?

    # Skip processing for non-user messages
    if msg.role != "user":
        return msg

    # Skip path processing for commands (single slash in first word)
    # Examples: /shell, /log, /python - these are commands
    # But /path/to/file.md (multiple slashes) is a file path, not a command
    from .content import is_message_command  # fmt: skip

    if is_message_command(msg.content):
        return msg

    append_msg = ""
    files = []

    # Find potential paths in message
    for word in _find_potential_paths(msg.content):
        logger.debug(f"potential path/url: {word=}")
        # If not using fresh context, include text file contents in the message
        if not use_fresh_context() and (contents := _resource_to_codeblock(word)):
            append_msg += "\n\n" + contents
        else:
            # if we found an non-text file, include it in msg.files
            file = _parse_prompt_files(word)
            if file:
                # Store path relative to workspace if provided
                file = file.expanduser()
                if workspace and not file.is_absolute():
                    file = file.absolute().relative_to(workspace)
                files.append(file)

    if files:
        msg = msg.replace(files=msg.files + files)

    # append the message with the file contents
    if append_msg:
        msg = msg.replace(content=msg.content + append_msg)

    return msg


def _find_potential_paths(content: str) -> list[str]:
    """
    Find potential file paths and URLs in a message content.
    Excludes content within code blocks.

    Args:
        content: The message content to search

    Returns:
        List of potential paths/URLs found in the message
    """
    # Remove code blocks to avoid matching paths inside them
    # TODO: also remove paths inside XML tags
    re_codeblock = r"````?[\s\S]*?\n````?"
    assert re.match(
        re_codeblock, md_codeblock("test", "test")
    ), "Code block regex should match the md_codeblock format with quadruple backticks"
    assert re.match(
        re_codeblock, md_codeblock("test", "test").replace("````", "```")
    ), "Code block regex should match the md_codeblock format with triple backticks"

    content_no_codeblocks = re.sub(re_codeblock, "", content)

    # List current directory contents for relative path matching
    cwd_files = [f.name for f in Path.cwd().iterdir()]

    paths = []

    def is_path_like(word: str) -> bool:
        """Helper to check if a word looks like a path"""
        return (
            # Absolute/home/relative paths
            any(word.startswith(s) for s in ["/", "~/", "./"])
            # URLs
            or word.startswith("http")
            # Contains slash (for backtick-wrapped paths)
            or "/" in word
            # Files in current directory or subdirectories
            or any(word.split("/", 1)[0] == file for file in cwd_files)
        )

    # First find backtick-wrapped content
    for match in re.finditer(r"`([^`]+)`", content_no_codeblocks):
        word = match.group(1).strip()
        word = word.rstrip("?").rstrip(".").rstrip(",").rstrip("!")
        if is_path_like(word):
            paths.append(word)

    # Then find non-backtick-wrapped words
    # Remove backtick-wrapped content first to avoid double-processing
    content_no_backticks = re.sub(r"`[^`]+`", "", content_no_codeblocks)
    for word in re.split(r"\s+", content_no_backticks):
        word = word.strip()
        word = word.rstrip("?").rstrip(".").rstrip(",").rstrip("!")
        if not word:
            continue

        if is_path_like(word):
            paths.append(word)

    return paths


def _resource_to_codeblock(prompt: str) -> str | None:
    """
    Takes a string that might be a path or URL,
    and if so, returns the contents of that file wrapped in a codeblock.
    """

    try:
        # check if prompt is a path, if so, replace it with the contents of that file
        f = Path(prompt).expanduser()
        if f.exists() and f.is_file():
            return md_codeblock(prompt, f.read_text())
    except OSError as oserr:
        # some prompts are too long to be a path, so we can't read them
        if oserr.errno == errno.ENAMETOOLONG:
            return None
        raise
    except UnicodeDecodeError:
        # some files are not text files (images, audio, PDFs, binaries, etc), so we can't read them
        # TODO: but can we handle them better than just printing the path? maybe with metadata from `file`?
        # logger.warning(f"Failed to read file {prompt}: not a text file")
        return None

    # check if any word in prompt is a path or URL,
    # if so, append the contents as a code block
    words = prompt.split()
    paths = []
    urls = []
    for word in words:
        f = Path(word).expanduser()
        if f.exists() and f.is_file():
            paths.append(word)
            continue
        try:
            p = urllib.parse.urlparse(word)
            if p.scheme and p.netloc:
                urls.append(word)
        except ValueError:
            pass

    result = ""
    if paths or urls:
        result += "\n\n"
        if paths:
            logger.debug(f"{paths=}")
        if urls:
            logger.debug(f"{urls=}")
    for path in paths:
        result += _resource_to_codeblock(path) or ""

    for url in urls:
        content = None

        # First try to handle GitHub issues/PRs with specialized tools
        github_info = parse_github_url(url)
        if github_info:
            if github_info["type"] == "issues":
                content = get_github_issue_content(
                    github_info["owner"], github_info["repo"], github_info["number"]
                )
            elif github_info["type"] == "pull":
                content = get_github_pr_content(url)

        # If GitHub handling failed or not a GitHub issue/PR, fall back to browser
        if not content and has_tool("browser"):
            try:
                # Transform GitHub blob URLs to raw URLs
                transformed_url = transform_github_url(url)
                if transformed_url != url:
                    logger.debug(f"Transformed GitHub URL: {url} -> {transformed_url}")
                content = read_url(transformed_url)
            except Exception as e:
                logger.warning(f"Failed to read URL {url}: {e}")
        elif not content and not has_tool("browser"):
            logger.warning("Browser tool not available, skipping URL read")

        if content:
            result += md_codeblock(url, content)

    return result


def _parse_prompt_files(prompt: str) -> Path | None:
    """
    Takes a string that might be a supported file path (image, text, PDF) and returns the path.
    Files added here will either be included inline (legacy mode) or in fresh context (fresh context mode).
    """
    try:
        p = Path(prompt).expanduser()
        if not (p.exists() and p.is_file()):
            return None

        # Try to read as text
        try:
            p.read_text()
            return p
        except UnicodeDecodeError:
            # If not text, check if supported binary format
            if p.suffix[1:].lower() in ["png", "jpg", "jpeg", "gif", "pdf"]:
                return p
            return None
    except OSError as oserr:  # pragma: no cover
        # some prompts are too long to be a path, so we can't read them
        if oserr.errno != errno.ENAMETOOLONG:
            return None
        raise
