"""
Gives the assistant the ability to present a form with multiple fields for user input.

This tool enables structured data collection from users through an interactive form
with support for different field types: text, select, boolean, and number.
"""

import json
import re
from collections.abc import Generator

from ..message import Message
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)

instructions = """
Present a form with multiple fields for user input.

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
{ToolUse("form", [], '''name: Project name?
description: Brief description?
language: Primary language [python, javascript, rust, go, other]
priority: Priority level [low, medium, high]''').to_output(tool_format)}
> System: Form submitted:
```json
{{"name": "my-awesome-project", "description": "A CLI tool for productivity", "language": "python", "priority": "high"}}
```

### Simple confirmation form

> User: Deploy to production
> Assistant: Please confirm the deployment details:
{ToolUse("form", [], '''environment: Target environment [staging, production]
confirm: Proceed with deployment? [yes/no]''').to_output(tool_format)}
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
        if set(opt.lower() for opt in options) == {"yes", "no"}:
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


def execute_form(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Present a form to the user and collect their responses."""
    if not code:
        yield Message("system", "No form fields provided")
        return

    fields = parse_form_content(code)
    if not fields:
        yield Message("system", "No valid fields found in form definition")
        return

    # Import questionary here to handle import errors gracefully
    try:
        import questionary
    except ImportError:
        from ..util.install import get_package_install_command

        install_cmd = get_package_install_command("questionary")
        yield Message(
            "system",
            f"questionary library not available. Please install it with: {install_cmd}",
        )
        return

    results: dict = {}

    try:
        for field in fields:
            name = field["name"]
            prompt = field["prompt"]
            field_type = field["type"]

            if field_type == "select":
                value = questionary.select(
                    prompt,
                    choices=field["options"],
                    use_shortcuts=True,
                ).ask()
            elif field_type == "boolean":
                value = questionary.confirm(prompt, default=False).ask()
            elif field_type == "number":
                while True:
                    value_str = questionary.text(prompt).ask()
                    if value_str is None:
                        value = None
                        break
                    try:
                        # Try int first, then float
                        if "." in value_str:
                            value = float(value_str)
                        else:
                            value = int(value_str)
                        break
                    except ValueError:
                        questionary.print("Please enter a valid number")
            else:  # text
                value = questionary.text(prompt).ask()

            if value is None:
                yield Message("system", "Form cancelled")
                return

            results[name] = value

        # Format results as JSON
        results_json = json.dumps(results, indent=2)
        yield Message("system", f"Form submitted:\n```json\n{results_json}\n```")

    except (KeyboardInterrupt, EOFError):
        yield Message("system", "Form cancelled")
    except Exception as e:
        yield Message("system", f"Error during form input: {e}")


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
