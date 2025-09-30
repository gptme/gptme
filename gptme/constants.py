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

PROMPT_USER = f"[bold {ROLE_COLOR['user']}]User[/bold {ROLE_COLOR['user']}]"


def prompt_assistant(name: str | None) -> str:
    if not name:
        name = os.environ.get("GPTME_AGENT_NAME", "Assistant")
    return f"[bold {ROLE_COLOR['assistant']}]{name}[/bold {ROLE_COLOR['assistant']}]"


INTERRUPT_CONTENT = "Interrupted by user"

# Image file extensions
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"]
