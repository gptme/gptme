"""
Constants
"""

import os

# Optimized for code
# Discussion here: https://community.openai.com/t/cheat-sheet-mastering-temperature-and-top-p-in-chatgpt-api-a-few-tips-and-tricks-on-controlling-the-creativity-deterministic-output-of-prompt-responses/172683
# NOTE: getting the environment variables like this ignores if they are set in the gptme config
TEMPERATURE = float(os.getenv("TEMPERATURE", "0"))
TOP_P = float(os.getenv("TOP_P", "0.1"))

# separator for multiple rounds of prompts on the command line
# demarcates the end of the user's prompt, and start of the assistant's response
# e.g. /gptme "generate a poem" "-" "save it to poem.txt"
# where the assistant will generate a poem, and then save it to poem.txt
MULTIPROMPT_SEPARATOR = "-"

# Prompts
ROLE_COLOR = {
    "user": "green",
    "assistant": os.environ.get("GPTME_AGENT_COLOR", "green"),
    "system": "grey42",
}


def prompt_user(name: str | None = None) -> str:
    from rich.markup import escape

    if not name:
        name = "User"
    return f"[bold {ROLE_COLOR['user']}]{escape(name)}[/bold {ROLE_COLOR['user']}]"


PROMPT_USER = prompt_user()


def prompt_assistant(name: str | None) -> str:
    if not name:
        name = os.environ.get("GPTME_AGENT_NAME", "Assistant")
    return f"[bold {ROLE_COLOR['assistant']}]{name}[/bold {ROLE_COLOR['assistant']}]"


INTERRUPT_CONTENT = "Interrupted by user"

# Content for when user declines execution (behaves like interrupt - returns to prompt)
DECLINED_CONTENT = "Execution declined by user"

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
