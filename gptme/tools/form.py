"""
Gives the assistant the ability to present a form with multiple fields for user input.

This tool enables structured data collection from users through an interactive form
with support for different field types: text, select, boolean, and number.
"""

import json
import math
import re
from collections.abc import Generator

from ..hooks.elicitation import ElicitationRequest, FormField, elicit
from ..message import Message
from .base import (
    Parameter,
    ToolSpec,
    ToolUse,
)

instructions = """
### When to use the form tool

Use the form tool when you need to collect multiple related fields from the
user in a single interaction. It is well suited for structured data collection
where field types are text, select (choose from a list), boolean (yes/no), or
number.

For **secrets** (API keys, passwords) use the ``elicit`` tool instead — form
does not have a secret type, so credentials would appear in the chat display.

For a single question or free-form input, a plain assistant message is simpler.
Prefer form when collecting two or more related fields at once.

### Form syntax

Each field is specified on a separate line with the format:
  field_name: Prompt text [options]

Field types are inferred from the prompt:
- Text field (default): `name: What's your name?`
- Select field: `priority: Priority level [low, medium, high]`
- Boolean field: `confirm: Are you sure? [yes/no]`
- Number field: `count: How many? (number)`

The tool will present an interactive form and return the collected data as JSON.
""".strip()

instructions_format = {
    "markdown": "Use a code block with the language tag: `form` followed by field definitions.",
}


def examples(tool_format):
    return f"""
### Collect project information

> User: I want to start a new project
> Assistant: Let me gather some information about your project:
{
        ToolUse(
            "form",
            [],
            '''name: Project name?
description: Brief description?
language: Primary language [python, javascript, rust, go, other]
priority: Priority level [low, medium, high]''',
        ).to_output(tool_format)
    }
> System: Form submitted:
```json
{{"name": "my-awesome-project", "description": "A CLI tool for productivity", "language": "python", "priority": "high"}}
```

### Simple confirmation form

> User: Deploy to production
> Assistant: Please confirm the deployment details:
{
        ToolUse(
            "form",
            [],
            '''environment: Target environment [staging, production]
confirm: Proceed with deployment? [yes/no]''',
        ).to_output(tool_format)
    }
> System: Form submitted:
```json
{{"environment": "production", "confirm": true}}
```
""".strip()


def parse_field(line: str) -> dict | None:
    """Parse a field definition line into a field specification."""
    line = line.strip()
    if not line or ":" not in line:
        return None

    # Split on first colon
    name, rest = line.split(":", 1)
    name = name.strip()
    rest = rest.strip()

    if not name or not rest:
        return None

    # Check for select options [opt1, opt2, ...]
    select_match = re.search(r"\[([^\]]+)\]", rest)
    if select_match:
        options_str = select_match.group(1)
        prompt = rest[: select_match.start()].strip()
        options = [opt.strip() for opt in options_str.split(",")]

        # Check if it's a boolean field [yes/no]
        if re.fullmatch(r"yes\s*/\s*no", options_str.strip(), re.IGNORECASE) or {
            opt.lower() for opt in options
        } == {"yes", "no"}:
            return {
                "name": name,
                "prompt": prompt or f"{name}?",
                "type": "boolean",
            }
        return {
            "name": name,
            "prompt": prompt or f"Select {name}:",
            "type": "select",
            "options": options,
        }

    # Check for number field (number)
    if "(number)" in rest.lower():
        prompt = rest.replace("(number)", "").replace("(Number)", "").strip()
        return {
            "name": name,
            "prompt": prompt or f"Enter {name}:",
            "type": "number",
        }

    # Default to text field
    return {
        "name": name,
        "prompt": rest,
        "type": "text",
    }


def parse_form_content(content: str) -> list[dict]:
    """Parse form content into a list of field specifications."""
    fields = []
    for line in content.strip().split("\n"):
        field = parse_field(line)
        if field:
            fields.append(field)
    return fields


def _to_form_field(field: dict) -> FormField:
    """Convert a parsed field spec into an elicitation ``FormField``."""
    field_type = field["type"]
    if field_type == "select":
        return FormField(
            name=field["name"],
            prompt=field["prompt"],
            type="choice",
            options=field["options"],
        )
    if field_type == "boolean":
        # Unanswered booleans default to "no", never an implicit approval
        return FormField(
            name=field["name"], prompt=field["prompt"], type="boolean", default=False
        )
    if field_type == "number":
        return FormField(name=field["name"], prompt=field["prompt"], type="number")
    # Text fields may be left empty
    return FormField(
        name=field["name"], prompt=field["prompt"], type="text", required=False
    )


def _field_type_error(value: object, field: FormField) -> str | None:
    """Return an error description if ``value`` does not fit ``field``'s type."""
    if field.type == "number":
        # bool is a subclass of int, but a boolean is not a number here.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"must be a number, got {type(value).__name__}"
        if isinstance(value, float) and not math.isfinite(value):
            return "must be a finite number"
    elif field.type == "boolean":
        if not isinstance(value, bool):
            return f"must be a boolean, got {type(value).__name__}"
    elif field.type == "choice":
        if not isinstance(value, str):
            return f"must be a string, got {type(value).__name__}"
        if field.options and value not in field.options:
            return f"must be one of {field.options}"
    elif not isinstance(value, str):
        return f"must be a string, got {type(value).__name__}"
    return None


def _validate_form_results(
    results: dict[str, object], fields: list[FormField]
) -> str | None:
    """Check a client-supplied form answer against the requested fields.

    An interface answers with its own JSON, so nothing guarantees it honoured the
    field names or types. Reporting an unchecked payload as "Form submitted"
    would hand the model values it never asked for (``{}`` skipping a required
    field, ``"false"`` as a boolean, ``"abc"`` as a number).

    Returns a human-readable error, or ``None`` when the answer is complete.
    """
    for field in fields:
        value = results.get(field.name)
        if value is None:
            if field.required:
                return f"missing required field '{field.name}'"
            continue
        error = _field_type_error(value, field)
        if error:
            return f"field '{field.name}' {error}"
    return None


def execute_form(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Present a form to the user and collect their responses."""
    if code is None and kwargs:
        code = kwargs.get("fields")
    if not code:
        yield Message("system", "No form fields provided")
        return

    fields = parse_form_content(code)
    if not fields:
        yield Message("system", "No valid fields found in form definition")
        return

    # Ask through the elicitation hooks so every surface (CLI, TUI, server/web UI)
    # renders the form in its own UI instead of the tool prompting the terminal.
    request = ElicitationRequest(
        type="form",
        prompt="",
        fields=[_to_form_field(field) for field in fields],
    )
    try:
        response = elicit(request)
    except (KeyboardInterrupt, EOFError):
        yield Message("system", "Form cancelled")
        return
    except Exception as e:
        yield Message("system", f"Error during form input: {e}")
        return

    if response.cancelled or response.value is None:
        yield Message("system", "Form cancelled")
        return

    try:
        results = json.loads(response.value)
    except json.JSONDecodeError:
        yield Message("system", "Error during form input: invalid form response")
        return
    if not isinstance(results, dict):
        yield Message("system", "Error during form input: invalid form response")
        return

    validation_error = _validate_form_results(results, request.fields or [])
    if validation_error:
        yield Message("system", f"Error during form input: {validation_error}")
        return

    results_json = json.dumps(results, indent=2)
    yield Message("system", f"Form submitted:\n```json\n{results_json}\n```")


tool_form = ToolSpec(
    name="form",
    desc="Present a form with multiple fields for user input",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_form,
    block_types=["form"],
    disabled_by_default=True,
    parameters=[
        Parameter(
            name="fields",
            type="string",
            description="Form field definitions, one per line",
            required=True,
        ),
    ],
)

__doc__ = tool_form.get_doc(__doc__)
