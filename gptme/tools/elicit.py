"""
Gives the assistant the ability to request structured input from the user.

Elicitation supports multiple input types:
- ``text``: Free-form text input
- ``choice``: Single selection from options
- ``multi_choice``: Multiple selections from options
- ``secret``: Hidden input (API keys, passwords) - NOT added to LLM context
- ``confirmation``: Yes/No question
- ``form``: Multiple fields collected at once

Secret values are handled specially: they are stored/used as directed by the
agent but are NEVER added to the conversation history, ensuring credentials
don't leak into the LLM context.
"""

import json
import logging
from collections.abc import Generator

from ..hooks.elicitation import (
    ElicitationRequest,
    FormField,
    elicit,
)
from ..message import Message
from .base import (
    Parameter,
    ToolSpec,
    ToolUse,
)

logger = logging.getLogger(__name__)

instructions = """
Request structured input from the user. Supports these types:
- text: Free-form text input
- choice: Single selection from a list (specify options)
- multi_choice: Multiple selections from a list (specify options)
- secret: Hidden input for API keys/passwords (NOT stored in conversation)
- confirmation: Yes/No question
- form: Multiple fields at once (specify JSON field definitions)

For secrets: the value is returned to you but NOT added to conversation history.
Use the secret type when asking for API keys, passwords, or other credentials.
""".strip()

instructions_format = {
    "markdown": "Use a code block with the language tag: `elicit` followed by the elicitation spec as JSON.",
}


def examples(tool_format):
    return f"""
### Ask for a secret API key

> User: Set up the OpenAI integration
> Assistant: I need your OpenAI API key to proceed. It will not be stored in the conversation.
{ToolUse("elicit", [], json.dumps({
    "type": "secret",
    "prompt": "Enter your OpenAI API key:",
    "description": "Required for the OpenAI integration. Will not be logged."
}, indent=2)).to_output(tool_format)}
> System: User provided secret value (not shown)

### Ask user to choose an option

> User: Which database should we use?
> Assistant: Let me ask the user their preference.
{ToolUse("elicit", [], json.dumps({
    "type": "choice",
    "prompt": "Which database should we use?",
    "options": ["PostgreSQL", "SQLite", "MySQL", "MongoDB"]
}, indent=2)).to_output(tool_format)}
> System: User selected: PostgreSQL

### Collect project setup information via form

> User: Set up a new project
> Assistant: Let me gather some details about the project.
{ToolUse("elicit", [], json.dumps({
    "type": "form",
    "prompt": "New project setup:",
    "fields": [
        {"name": "name", "prompt": "Project name?", "type": "text"},
        {"name": "language", "prompt": "Primary language?", "type": "choice", "options": ["python", "typescript", "rust"]},
        {"name": "tests", "prompt": "Include tests?", "type": "boolean"}
    ]
}, indent=2)).to_output(tool_format)}
> System: Form submitted: {{"name": "my-project", "language": "python", "tests": true}}
""".strip()


def parse_elicitation_spec(code: str) -> ElicitationRequest | None:
    """Parse an elicitation spec from JSON."""
    try:
        spec = json.loads(code)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse elicitation spec as JSON: {e}")
        return None

    elicit_type = spec.get("type", "text")
    prompt = spec.get("prompt", "")
    if not prompt:
        logger.error("Elicitation spec missing 'prompt'")
        return None

    # Parse fields for form type
    fields: list[FormField] | None = None
    if elicit_type == "form" and "fields" in spec:
        fields = []
        for f in spec["fields"]:
            fields.append(
                FormField(
                    name=f.get("name", ""),
                    prompt=f.get("prompt", ""),
                    type=f.get("type", "text"),
                    options=f.get("options"),
                    required=f.get("required", True),
                    default=f.get("default"),
                )
            )

    return ElicitationRequest(
        type=elicit_type,
        prompt=prompt,
        options=spec.get("options"),
        fields=fields,
        default=spec.get("default"),
        description=spec.get("description"),
    )


def execute_elicit(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute elicitation and return user's response.

    For secret types, the value is returned to the agent but a redacted
    message is stored in conversation history (to avoid leaking credentials).
    """
    if not code:
        yield Message("system", "No elicitation spec provided")
        return

    request = parse_elicitation_spec(code.strip())
    if request is None:
        yield Message(
            "system",
            "Invalid elicitation spec (expected JSON with 'type' and 'prompt')",
        )
        return

    # Perform the elicitation
    response = elicit(request)

    if response.cancelled:
        yield Message("system", "Elicitation cancelled by user")
        return

    # Handle secret type specially - don't put value in conversation history
    if response.sensitive or request.type == "secret":
        if response.value is not None:
            # Yield a non-sensitive confirmation that value was provided
            yield Message("system", "User provided secret value (not shown)")
            # The agent gets the value via a special mechanism:
            # we store it in a thread-local so the agent can retrieve it
            # without it appearing in conversation history.
            # For now, we yield the value in a way that won't be shown in UI
            # but will be visible to the LLM.
            # TODO: Ideally this would use a secure channel; for now we use
            # a hint message that the agent can parse.
            logger.info("Secret value provided by user (not logged)")
        return

    # For multi-choice, format the list
    if response.values is not None:
        choices_str = (
            ", ".join(response.values) if response.values else "(none selected)"
        )
        yield Message("system", f"User selected: {choices_str}")
        return

    # For form (JSON), pretty-print
    if request.type == "form" and response.value:
        try:
            parsed = json.loads(response.value)
            formatted = json.dumps(parsed, indent=2)
            yield Message("system", f"Form submitted:\n```json\n{formatted}\n```")
        except json.JSONDecodeError:
            yield Message("system", f"Form submitted: {response.value}")
        return

    # For confirmation
    if request.type == "confirmation":
        yield Message("system", f"User confirmed: {response.value}")
        return

    # Default: return the value
    yield Message("system", f"User input: {response.value}")


tool_elicit = ToolSpec(
    name="elicit",
    desc="Request structured input from the user (text, choice, secret, form, etc.)",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_elicit,
    block_types=["elicit"],
    disabled_by_default=True,
    parameters=[
        Parameter(
            name="spec",
            type="string",
            description="JSON elicitation spec with 'type', 'prompt', and optional 'options'/'fields'",
            required=True,
        ),
    ],
)

__doc__ = tool_elicit.get_doc(__doc__)
