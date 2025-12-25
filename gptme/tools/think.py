"""
This tool enables deeper thinking on complex problems by sending requests to more powerful LLMs.

The tool allows the assistant to request help with complex reasoning tasks by:
1. Sending the current context and problem to a more powerful model
2. Getting back enhanced reasoning and analysis
3. Using that to provide better solutions
"""

# TODO: Implement Karpathy-style consortium
# - Send the same problem to multiple models
# - Aggregate and compare their responses
# - Use voting/consensus mechanisms
# - Consider different specialties/capabilities

import logging
import time
from collections.abc import Generator

from ..llm import reply
from ..llm.models import get_model
from ..message import Message
from .base import ConfirmFunc, Parameter, ToolSpec

logger = logging.getLogger(__name__)


def execute(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute the think tool with the given content."""
    if not code:
        yield Message("system", "No content provided for thinking")
        return

    default_model = "openai/o1-preview"

    # Get current model
    current_model = get_model()

    # Use specialized reasoning models if available
    # TODO: choose reasoner based on provider, similar to summary models
    if kwargs and (m := kwargs.get("model")):
        model = m
    elif args and len(args) == 1:
        model = args[0].split("=", 1)[-1]
    else:
        model = default_model

    reasoning_model = get_model(model)

    if reasoning_model.model == current_model.model:
        logger.warning(
            "No more powerful model available for reasoning, using current model"
        )

    # Extract any referenced files from the content
    files_content = ""
    files_block = False
    for line in code.split("\n"):
        if line.strip().startswith("Files:"):
            files_block = True
            continue
        if not files_block:
            continue
        if line.strip().startswith("- "):
            filepath = line.strip("- ").strip()
            # check for (comment) suffix after path
            if filepath.endswith(")") and " (" in filepath:
                filepath = filepath.split(" (")[0].strip()
            try:
                with open(filepath) as f:
                    files_content += (
                        f"\nContents of {filepath}:\n```\n{f.read()}\n```\n"
                    )
            except Exception as e:
                logger.warning(f"Failed to read file {filepath}: {e}")
        else:
            files_block = False

    # Combine file contents with the thinking request
    if files_content:
        full_content = files_content + "\n" + code
    else:
        full_content = code

    # Prepare thinking prompt
    thinking_prompt = f"""Please analyze this problem carefully and thoroughly:

{full_content}

Think step by step and consider:
1. Key aspects and components
2. Potential challenges and edge cases
3. Alternative approaches
4. Best practices and principles
5. Implementation details"""

    # Log that we're using a different model for thinking
    logger.info(f"Using {reasoning_model.full} for enhanced reasoning")

    # Get enhanced reasoning
    messages = [Message("user", thinking_prompt)]
    start = time.monotonic()
    response = reply(messages, reasoning_model.full, stream=False)
    logger.info(f"Thought for {time.monotonic() - start:.2f}s")

    # Return the enhanced reasoning
    yield Message(
        "system",
        f"Enhanced reasoning from {reasoning_model.model}:\n\n{response.content}",
    )


tool = ToolSpec(
    "think",
    desc="Tool to enable deeper thinking on challenging or complex problems.",
    block_types=["think"],
    execute=execute,
    instructions="""
Use this tool when you need help with complex reasoning or problem-solving.
Wrap your thinking request in a code block with the language tag: `think`

IMPORTANT: You must include relevant files under a "Files:" section, as state is not preserved between calls.
The tool will read and include the contents of listed files in the analysis.

The tool will:
1. Send your request (and file contents) to a more powerful model
2. Get back enhanced reasoning and analysis
3. Help you provide better solutions

You can specify the model using the `model` parameter.

Example:
```think model=openai/o1-preview
How should we architect this system to be scalable and maintainable?
Key considerations:
- Multiple users
- Real-time updates
- Data consistency
- Error handling

Files:
- README.md (project overview and requirements)
- src/api.py (current API implementation)
- docs/architecture.md (existing architecture docs)
```

Note: Always include relevant files, as the tool cannot access files from previous calls.
""",
    parameters=[
        Parameter(
            "model",
            type="string",
            description="Model to use for enhanced reasoning (i.e. o1-preview)",
            required=False,
        )
    ],
)
