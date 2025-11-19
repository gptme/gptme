import io
import logging
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, HTML, to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style
from pygments.lexer import RegexLexer
from pygments.token import Name, Text
from rich.console import Console

from gptme.config import get_config
from gptme.llm.models import get_default_model

from ..dirs import get_pt_history_file

# Make cache management functions available at module level
__all__ = ["clear_path_cache", "check_cwd", "is_valid_path", "PathLexer"]


logger = logging.getLogger(__name__)

# Cache management
_last_cwd: str | None = None
_last_check = 0.0
_pwd_files: set[str] | None = None
CHECK_INTERVAL = 1.0  # seconds


def _get_pwd_files() -> set[str]:
    """Get a cached set of filenames in the current directory."""
    global _pwd_files
    if _pwd_files is None:
        _pwd_files = {f.name for f in Path.cwd().glob("*")}
        # logger.debug(f"Updated pwd files cache: {_pwd_files}")
    return _pwd_files


def clear_path_cache() -> None:
    """Clear the path validation cache.

    This should be called when:
    - The working directory changes
    - Files are created or deleted
    - The filesystem changes significantly
    """
    global _last_cwd, _last_check, _pwd_files
    is_valid_path.cache_clear()
    _last_cwd = None
    _last_check = 0
    _pwd_files = None
    # logger.debug("Path validation cache cleared")


def check_cwd() -> None:
    """Check if working directory has changed and clear cache if needed."""
    global _last_cwd, _last_check
    current_time = time.time()
    current = os.getcwd()

    # Always check on first run
    if _last_cwd is None:
        _last_check = current_time
        _last_cwd = current
        clear_path_cache()
        return

    # Check if enough time has passed
    if current_time - _last_check >= CHECK_INTERVAL:
        _last_check = current_time
        if _last_cwd != current:
            logger.debug(f"Working directory changed from {_last_cwd} to {current}")
            clear_path_cache()
            _last_cwd = current


# TODO: this should prob have a cache, but breaks tests which mutate files, so maybe not
@lru_cache(maxsize=1024)
def is_valid_path(path_str: str) -> bool:
    """Check if a string represents a valid path.

    Args:
        path_str: The string to check

    Returns:
        bool: True if the string represents a valid path that exists

    The function checks if:
    - The path exists directly
    - The path is a valid symlink that resolves to an existing path
    - For new files: parent directory must exist and be writable

    It handles:
    - Absolute paths (/path/to/file)
    - Home directory paths (~/path)
    - Relative paths (./path, ../path, path/to/file)
    - Quoted paths ("path with spaces")
    - Escaped spaces (path\\ with\\ spaces)
    - Windows paths (C:\\path\\to\\file)
    """
    try:
        # Skip strings that are clearly not paths
        if len(path_str) < 2:
            return False

        # Basic sanity check for valid path characters
        # Allow spaces and more characters in paths
        if not re.match(r"^[\w./\-\\ ~'\"\[\](){},@:]+$", path_str):
            return False

        # For simple filenames without path separators, check if file or directory exists
        if "/" not in path_str and "\\" not in path_str:
            try:
                # Check pwd cache for files
                if path_str in _get_pwd_files() and Path(path_str).resolve().exists():
                    logger.debug(f"File exists in current directory: {path_str}")
                    return True
                # Also check if it's a directory
                if Path(path_str).is_dir():
                    logger.debug(f"Directory exists: {path_str}")
                    return True
                return False
            except Exception as e:
                logger.debug(f"Error checking current directory: {e}")
                return False

        # Handle escaped spaces and quotes
        if r"\ " in path_str:
            path_str = path_str.replace(r"\ ", " ")
        if path_str.startswith('"') and path_str.endswith('"'):
            path_str = path_str[1:-1]
        elif path_str.startswith("'") and path_str.endswith("'"):
            path_str = path_str[1:-1]

        # Handle home directory expansion
        if path_str.startswith("~"):
            path_str = os.path.expanduser(path_str)

        # Convert to Path object
        path = Path(path_str)

        # Handle relative paths
        if not path.is_absolute():
            path = Path.cwd() / path

        # Check if path exists directly
        if path.exists():
            logger.debug(f"Path exists directly: {path_str}")
            return True

        # Check if it's a symlink and resolves to an existing file
        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
                exists = resolved.exists()
                logger.debug(f"Symlink {path_str} -> {resolved} exists: {exists}")
                return exists
            except Exception:
                logger.debug(f"Invalid symlink: {path_str}")
                return False

        # Path doesn't exist and isn't a valid symlink
        return False

    except Exception as e:
        logger.debug(f"Path validation failed for '{path_str}': {e}")
        return False


@dataclass
class PathMatch:
    """Represents a matched path in text."""

    start: int
    end: int
    path: str
    quoted: bool = False
    escaped: bool = False


class PathLexer(RegexLexer):
    """Lexer that highlights valid file paths."""

    name = "Path"
    tokens = {
        "root": [
            # Match paths with various patterns
            (
                r"(?:"
                r'(?:/(?:[^/\s"\'\\]|\\[ ])+(?:/(?:[^/\s"\'\\]|\\[ ])+)*/?)|'  # Absolute paths
                r'(?:~/(?:[^/\s"\'\\]|\\[ ])+(?:/(?:[^/\s"\'\\]|\\[ ])+)*/?)|'  # Home paths
                r'(?:\.\.?/(?:[^/\s"\'\\]|\\[ ])+(?:/(?:[^/\s"\'\\]|\\[ ])+)*/?)|'  # ./ and ../
                r'(?:"[^"\n]*")|'  # Double-quoted paths
                r"(?:'[^'\n]*')|"  # Single-quoted paths
                r'(?:[^/\s"\'\\](?:[^/\s"\'\\]|\\[ ])*(?:/(?:[^/\s"\'\\]|\\[ ])+)*/?)|'  # Simple names and paths
                r'(?:[a-zA-Z]:\\(?:[^\\\/\s"\'\\]|\\[ ])+(?:\\(?:[^\\\/\s"\'\\]|\\[ ])+)*\\?)'  # Windows paths
                r")",
                Name.Variable,
            ),
            (r"[^\s]+", Text),  # Non-whitespace text
            (r"\s+", Text),  # Whitespace
        ]
    }

    def get_tokens_unprocessed(
        self, text: str, stack=("root",)
    ) -> Iterator[tuple[int, str, str]]:
        """Generate tokens with path validation."""
        for index, token, value in RegexLexer.get_tokens_unprocessed(self, text, stack):
            if token is Name.Variable:
                # Extract path from value (handle quotes and escapes)
                path = value
                if path.startswith(("'", '"')) and path.endswith(path[0]):
                    path = path[1:-1]
                path = path.replace(r"\ ", " ")

                # Validate path
                if is_valid_path(path):
                    yield index, token, value
                else:
                    yield index, Text, value
            else:
                yield index, token, value

    def find_paths(self, text: str) -> Iterator[PathMatch]:
        """Find potential paths in text."""
        # Pattern for various path formats
        patterns = [
            # Absolute paths
            r'/(?:[^/\s"\'\\]|\\[ "])+(?:/(?:[^/\s"\'\\]|\\[ "])+)*/?',
            # Home paths
            r'~/(?:[^/\s"\'\\]|\\[ "])+(?:/(?:[^/\s"\'\\]|\\[ "])+)*/?',
            # Relative paths with ./ or ../
            r'\.\.?/(?:[^/\s"\'\\]|\\[ "])+(?:/(?:[^/\s"\'\\]|\\[ "])+)*/?',
            # Quoted paths
            r'"([^"\\]|\\.)+"|\'([^\'\\]|\\.)+\'',
            # Relative paths without ./
            r'(?:[^/\s"\'\\]|\\[ "])+(?:/(?:[^/\s"\'\\]|\\[ "])+)+/?',
            # Windows paths
            r'[a-zA-Z]:\\(?:[^\\\/\s"\'\\]|\\[ "])+(?:\\(?:[^\\\/\s"\'\\]|\\[ "])+)*\\?',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text):
                path = match.group(0)
                quoted = path.startswith(("'", '"'))
                escaped = bool(re.search(r"\\\s", path))

                if quoted:
                    path = path[1:-1]
                if escaped:
                    path = path.replace("\\ ", " ")

                yield PathMatch(
                    start=match.start(),
                    end=match.end(),
                    path=path,
                    quoted=quoted,
                    escaped=escaped,
                )


class PathCompleter2(PathCompleter):
    """PathCompleter which adds slash to directories."""

    def get_completions(self, document, complete_event):
        """Completed paths and ends directories with a slash."""
        for c in super().get_completions(document, complete_event):
            # ideally do this on the first tab, not sure why we have to tab twice
            if Path(document.text_before_cursor + c.text).is_dir():
                yield Completion(c.text + "/", c.start_position, display=c.display)
            else:
                yield c


class GptmeCompleter(Completer):
    """Completer that combines command, path and LLM suggestions."""

    def __init__(self):
        self.path_completer = PathCompleter2(expanduser=True)

    def get_completions(self, document, complete_event):
        from ..commands import get_user_commands  # fmt: skip

        document.get_word_before_cursor()
        text = document.text_before_cursor
        path_seg = text.split(" ")[-1]

        completions = get_user_commands()  # Already includes "/" prefix

        # Command completion
        if text.startswith("/"):
            for option in completions:
                if option.startswith(text):
                    # make the already typed part bold and underlined
                    html = f"<teal><u><b>{text}</b></u>{option[len(text) :]}</teal>"
                    yield Completion(
                        option,
                        start_position=-len(text),
                        display=HTML(html),
                    )

        # Path completion
        elif (
            # Handle absolute/home/parent paths
            any(path_seg.startswith(prefix) for prefix in ["../", "~/", "./", "/"])
            or
            # Handle simple files in pwd using cache
            (
                "/" not in path_seg
                and "\\" not in path_seg
                and any(f.startswith(path_seg) for f in _get_pwd_files())
            )
            or
            # Handle partial paths that might match files in subdirectories
            any(Path.cwd().glob(f"{path_seg}*"))
        ):
            yield from self.path_completer.get_completions(
                Document(path_seg), complete_event
            )

        # LLM suggestions
        elif len(text) > 2:
            try:
                suggestions = llm_suggest(text)
                if suggestions:
                    for suggestion in suggestions:
                        if suggestion.startswith(text):
                            yield Completion(
                                suggestion,
                                start_position=-len(text),
                                display_meta="AI suggestion",
                            )
            except Exception:
                # Fail silently if LLM suggestions timeout/fail
                pass


@lru_cache
def llm_suggest(text: str) -> list[str]:
    # TODO: Improve LLM suggestions
    from ..llm import _chat_complete  # fmt: skip
    from ..message import Message  # fmt: skip

    model = get_default_model()
    assert model, "No model loaded"

    enabled = get_config().get_env_bool("GPTME_SUGGEST_LLM")
    if enabled:
        content = _chat_complete(
            messages=[
                Message(
                    "system",
                    """You are to tab-complete the user prompt with a relevant query.
Respond with one entry per line.
No preambles or greetings, or postamble.
Only 10 lines.""",
                ),
                Message("user", text),
            ],
            model=model.full,
            tools=[],
        )
        return content.split("\n")
    return []


_prompt_session: PromptSession | None = None


def get_prompt_session() -> PromptSession:
    """Create a PromptSession with history and completion support."""
    global _prompt_session
    if not _prompt_session:
        history_path = get_pt_history_file()
        if not history_path.parent.exists():
            history_path.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(history_path))
        completer = GptmeCompleter()

        kb = KeyBindings()

        @kb.add("c-j")  # Add Ctrl+J support for newline
        def _(event):
            """Insert newline on Ctrl+J"""
            event.current_buffer.insert_text("\n")

        @kb.add("enter")
        def _(event):
            """Submit on Enter."""
            event.current_buffer.validate_and_handle()

        _prompt_session = PromptSession(
            history=history,
            completer=completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
            complete_style=CompleteStyle.READLINE_LIKE,
            multiline=True,
            key_bindings=kb,
        )
    return _prompt_session


def get_input(prompt: str) -> str:
    """Get input from user with completion support."""
    # Check working directory and clear cache if needed
    check_cwd()

    session = get_prompt_session()
    try:
        with patch_stdout(raw=True):
            result = session.prompt(
                to_formatted_text(
                    ANSI(rich_to_str(prompt.rstrip() + " ", color_system="256"))
                ),
                lexer=PygmentsLexer(PathLexer),
                style=Style.from_dict(
                    {
                        "pygments.name.variable": "#87afff underline",  # bright blue, bold for paths
                    }
                ),
                include_default_pygments_style=False,
                enable_suspend=True,
                enable_open_in_editor=True,
            )
        return result
    except (EOFError, KeyboardInterrupt) as e:
        # Re-raise EOFError to handle Ctrl+D properly
        if isinstance(e, EOFError):
            raise
        return ""


def add_history(line: str) -> None:
    """Add a line to the prompt_toolkit history."""
    session = get_prompt_session()
    session.history.append_string(line)


def rich_to_str(text: str | Any, **kwargs) -> str:
    """Convert rich text to ANSI string.

    Args:
        text: The text to convert, can be any type that rich can print
        **kwargs: Additional arguments passed to Console

    Returns:
        str: The text converted to ANSI escape sequences
    """
    # kwargs.setdefault("color_system", "256")
    # kwargs.setdefault("force_terminal", True)  # Ensure ANSI codes are generated
    console = Console(file=io.StringIO(), **kwargs)
    console.print(text, end="")
    return console.file.getvalue()  # type: ignore
