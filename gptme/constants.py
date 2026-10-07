"""
Constants
"""

import functools
import os

# Optimized for code
# Discussion here: https://community.openai.com/t/cheat-sheet-mastering-temperature-and-top-p-in-chatgpt-api-a-few-tips-and-tricks-on-controlling-the-creativity-deterministic-output-of-prompt-responses/172683
# NOTE: getting the environment variables like this ignores if they are set in the gptme config
TEMPERATURE = float(os.getenv("TEMPERATURE", "0"))
TOP_P = float(os.getenv("TOP_P", "0.1"))

# Verbosity level for GPT-5+ OpenAI models. One of "low", "medium", "high".
# Unset (default) means the parameter is not sent and OpenAI's default applies.
# NOTE: getting the environment variable like this ignores if it is set in the gptme config
# See: https://platform.openai.com/docs/guides/latest-model
OPENAI_VERBOSITY = os.getenv("OPENAI_VERBOSITY")

# separator for multiple rounds of prompts on the command line
# demarcates the end of the user's prompt, and start of the assistant's response
# e.g. /gptme "generate a poem" "-" "save it to poem.txt"
# where the assistant will generate a poem, and then save it to poem.txt
MULTIPROMPT_SEPARATOR = "-"

# Prompts
ROLE_COLOR = {
    "user": "green",
    "assistant": "green",  # GPTME_AGENT_COLOR: see configured_role_color
    "system": "grey42",
}


def valid_color(value: object, where: str) -> str | None:
    """Return *value* if it is a color Rich can parse, else warn and drop it.

    Accepts hex (``#e5a50a``), ``rgb(r,g,b)`` and color names. An invalid
    color (or a non-string, e.g. ``color = 123`` in TOML) must not stop gptme
    from starting or break rendering.
    """
    if value is None:
        return None
    if isinstance(value, str):
        from rich.color import Color, ColorParseError

        try:
            Color.parse(value)
            return value
        except (ColorParseError, ValueError, KeyError):
            pass
    import logging

    logging.getLogger(__name__).warning("Ignoring invalid %s color %r", where, value)
    return None


def color_to_hex(value: object) -> str | None:
    """Convert a validated Rich color to a CSS-compatible hex value."""
    if not isinstance(value, str) or not value:
        return None

    from rich.color import Color, ColorParseError

    try:
        return Color.parse(value).get_truecolor().hex
    except (ColorParseError, ValueError, KeyError, AttributeError, TypeError):
        return None


@functools.lru_cache(maxsize=8)
def _env_agent_color(value: str) -> str | None:
    # cached: validate (and warn about) each GPTME_AGENT_COLOR value once
    return valid_color(value, "GPTME_AGENT_COLOR")


def env_agent_color() -> str | None:
    """Validated ``GPTME_AGENT_COLOR``, or None if unset/invalid."""
    env = os.environ.get("GPTME_AGENT_COLOR")
    return _env_agent_color(env) if env else None


def configured_role_color(role: str) -> str | None:
    """Display color configured for a role, or None to use the default.

    The agent color comes from ``GPTME_AGENT_COLOR``, then ``[agent].color``
    in gptme.toml; the user color from ``[user].color``.
    """
    if role == "assistant" and (color := env_agent_color()):
        return color
    if role not in ("user", "assistant"):
        return None
    from .config import get_config

    config = get_config()
    if role == "user":
        return config.user.user.color
    agent = config.chat and config.chat.agent_config
    return agent.color if agent else None


def role_color(role: str) -> str:
    """Display color for a message role (configured, else ``ROLE_COLOR``)."""
    return configured_role_color(role) or ROLE_COLOR.get(role, "default")


def prompt_user(name: str | None = None) -> str:
    from rich.markup import escape

    if not name:
        name = "User"
    color = role_color("user")
    return f"[bold {color}]{escape(name)}[/bold {color}]"


# default-colored; built without prompt_user() so importing this module does
# not load the config
PROMPT_USER = f"[bold {ROLE_COLOR['user']}]User[/bold {ROLE_COLOR['user']}]"


def prompt_assistant(name: str | None) -> str:
    from rich.markup import escape

    if not name:
        name = os.environ.get("GPTME_AGENT_NAME", "Assistant")
    color = role_color("assistant")
    return f"[bold {color}]{escape(name)}[/bold {color}]"


INTERRUPT_CONTENT = "Interrupted by user"

# Content for when user declines execution (behaves like interrupt - returns to prompt)
DECLINED_CONTENT = "Execution declined by user"

# Prefix of the system message written when an interactive session recovers from
# an exhausted LLM provider error. `_should_prompt_for_input` treats this like
# interrupt/decline so the loop returns control to the user instead of
# auto-generating (which would hammer a still-down provider).
LLM_REQUEST_FAILED_PREFIX = "LLM request failed:"

# Prefix of the system message written when GPTME_MAX_STEPS is reached.
# `_should_prompt_for_input` treats this like interrupt/decline so the loop
# returns control to the user instead of generating an unrequested extra turn.
MAX_STEPS_STOP_PREFIX = "Stopped: reached max steps limit"

# Maximum length for user message content (characters)
# This prevents unbounded memory usage and context window overflow
# 100k characters ≈ 25k tokens for typical English text
MAX_MESSAGE_LENGTH = 100_000

# Maximum size for prompt queue to prevent unbounded growth from misbehaving hooks
MAX_PROMPT_QUEUE_SIZE = 100

# Size thresholds for URL/file content
# Content above INFO threshold logs info about the size
# Content above WARN threshold logs warning and gets truncated
CONTENT_SIZE_INFO_THRESHOLD = 50_000  # 50KB - log info
CONTENT_SIZE_WARN_THRESHOLD = 100_000  # 100KB - warn and truncate

# Maximum total characters to append via include_paths across all files/URLs
# Prevents context-window saturation when prompts reference many large files
INCLUDE_PATHS_MAX_CONTENT = 200_000  # 200KB
