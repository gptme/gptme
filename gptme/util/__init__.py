"""
Utility package for gptme.
"""

import functools
import logging
import re
import shutil
import textwrap
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from rich import print
from rich.console import Console

EMOJI_WARN = "⚠️"

logger = logging.getLogger(__name__)
console = Console(log_path=False)


def epoch_to_age(epoch, incl_date=False):
    # takes epoch and returns "x minutes ago", "3 hours ago", "yesterday", etc.
    age = datetime.now() - datetime.fromtimestamp(epoch)
    if age < timedelta(minutes=1):
        return "just now"
    elif age < timedelta(hours=1):
        return f"{age.seconds // 60} minutes ago"
    elif age < timedelta(days=1):
        return f"{age.seconds // 3600} hours ago"
    elif age < timedelta(days=2):
        return "yesterday"
    else:
        return f"{age.days} days ago" + (
            " ({datetime.fromtimestamp(epoch).strftime('%Y-%m-%d')})"
            if incl_date
            else ""
        )


def clean_example(s: str, strict=False, quote=False) -> str:
    orig = s
    s = re.sub(
        r"(^|\n)([>] )?([A-Za-z]+):",
        rf"\1{'> ' if quote else ''}\3:",
        s,
    )
    if strict:
        assert s != orig, "Couldn't find a message"
    return s


def example_to_xml(s: str) -> str:
    """
    Transforms an example with "> Role:" dividers into XML format <role>message</role>.
    """
    s = clean_example(s)
    orig = s
    print(f"After clean_example: {s!r}")  # Debug print

    lines = s.split("\n")
    result = []
    current_role = None
    current_message = []

    for line in lines:
        role_match = re.match(r"([A-Za-z]+):\s*(.*)", line)
        if role_match:
            if current_role and current_message:
                # Close previous role block - escape content for XML safety
                result.append(
                    f"<{current_role}>\n"
                    + xml_escape("\n".join(current_message))
                    + f"\n</{current_role}>"
                )
                current_message = []
            current_role = role_match.group(1).lower()
            current_message.append(role_match.group(2))
        else:
            if current_role:
                if line.strip() == "":
                    # Blank line indicates end of message - escape content for XML safety
                    result.append(
                        f"<{current_role}>\n"
                        + xml_escape("\n".join(current_message))
                        + f"\n</{current_role}>\n"
                    )
                    current_role = None
                    current_message = []
                else:
                    current_message.append(line)
            else:
                result.append(line)

    # Close any remaining role block - escape content for XML safety
    if current_role and current_message:
        result.append(
            f"<{current_role}>\n"
            + xml_escape("\n".join(current_message))
            + f"\n</{current_role}>\n"
        )

    s = "\n".join(result).strip()
    print(f"Final result: {s!r}")  # Debug print
    assert s != orig, "Couldn't find place to put start of directive"
    return s


def transform_examples_to_chat_directives(s: str, strict=False) -> str:
    """
    Transforms an example with "> Role:" dividers into ".. chat::" directive.
    """
    s = clean_example(s, strict=strict)
    s = textwrap.indent(s, "   ")
    orig = s
    s = re.sub(
        r"(^|\n)(   [# ]+(.+)(\n\s*)+)?   User:",
        r"\1\3\n\n.. chat::\n\n   User:",
        s,
    )
    if strict:
        assert s != orig, "Couldn't find place to put start of directive"
    return s


@lru_cache
def _is_sphinx_build() -> bool:
    """Check if the code is being executed in a Sphinx build."""
    try:
        # noreorder
        import sphinx  # fmt: skip

        is_sphinx = hasattr(sphinx, "application")
    except ImportError:
        is_sphinx = False
    # print(f"Is Sphinx build: {is_sphinx}")
    return is_sphinx


def document_prompt_function(*args, **kwargs):
    """Decorator for adding example output of prompts to docstrings in rst format"""

    def decorator(func):  # pragma: no cover
        # only do the __doc__ decoration if in a Sphinx build
        if not _is_sphinx_build():
            return func

        # noreorder
        from ..message import len_tokens  # fmt: skip
        from ..tools import init_tools  # fmt: skip

        init_tools()

        # Evaluate callable kwargs (for lazy evaluation)
        resolved_kwargs = {k: v() if callable(v) else v for k, v in kwargs.items()}

        prompt = "\n\n".join([msg.content for msg in func(*args, **resolved_kwargs)])
        prompt = textwrap.indent(prompt, "   ")
        # Use a default model for documentation purposes
        prompt_tokens = len_tokens(prompt, model="gpt-4")
        kwargs_str = (
            (" (" + ", ".join(f"{k}={v!r}" for k, v in kwargs.items()) + ")")
            if kwargs
            else ""
        )
        # unindent
        func.__doc__ = textwrap.dedent(func.__doc__ or "")
        func.__doc__ = func.__doc__.strip()
        func.__doc__ += f"\n\nExample output{kwargs_str}:"
        func.__doc__ += f"\n\n.. code-block:: markdown\n\n{prompt}"
        func.__doc__ += f"\n\nTokens: {prompt_tokens}"
        return func

    return decorator


def path_with_tilde(path: Path) -> Path:
    home = str(Path.home())
    path_str = str(path)
    if path_str.startswith(home):
        return Path(path_str.replace(home, "~", 1))
    return path


@functools.lru_cache
def get_installed_programs(candidates: tuple[str, ...]) -> set[str]:
    installed = set()
    for candidate in candidates:
        if shutil.which(candidate) is not None:
            installed.add(candidate)
    return installed
