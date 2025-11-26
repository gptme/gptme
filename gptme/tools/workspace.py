"""
Workspace navigation helper - shows workspace structure and key files.
"""

import os
from pathlib import Path

from ..message import Message
from . import ToolSpec, ToolUse


def instructions(tool_format):
    return f"""
Use this tool to get an overview of the current workspace structure.

The tool will show:
- Current working directory
- Key workspace files (README, ARCHITECTURE, etc.)
- Important directories and their purposes
- Quick navigation suggestions

Use a code block with the language tag: `workspace`
""".strip()


def examples(tool_format):
    return f"""
> User: show me the workspace structure
> Assistant:
{ToolUse("workspace", [], "").to_output(tool_format)}
> System: Workspace: /home/bob/alice
> Key Files:
> - README.md: Project overview
> - ARCHITECTURE.md: System design
> ...
""".strip()


def execute_workspace(
    code: str,
    args: list[str],
    kwargs: dict[str, str],
    confirm,
) -> Message:
    """Show workspace structure and key files."""
    cwd = Path.cwd()
    
    # Build output
    output_lines = [f"Workspace: {cwd}", ""]
    
    # Check for key files
    key_files = [
        ("README.md", "Project overview"),
        ("ARCHITECTURE.md", "System design"),
        ("ABOUT.md", "Agent identity"),
        ("TASKS.md", "Task management"),
        ("TOOLS.md", "Available tools"),
    ]
    
    found_files = []
    for filename, description in key_files:
        filepath = cwd / filename
        if filepath.exists():
            found_files.append(f"- {filename}: {description}")
    
    if found_files:
        output_lines.append("Key Files:")
        output_lines.extend(found_files)
        output_lines.append("")
    
    # Check for key directories
    key_dirs = [
        ("tasks", "Task definitions"),
        ("journal", "Daily logs"),
        ("knowledge", "Documentation"),
        ("lessons", "Behavioral patterns"),
        ("people", "Contact profiles"),
        ("projects", "Project symlinks"),
        ("state", "Work queues"),
    ]
    
    found_dirs = []
    for dirname, description in key_dirs:
        dirpath = cwd / dirname
        if dirpath.exists() and dirpath.is_dir():
            # Count items in directory
            try:
                items = list(dirpath.iterdir())
                count = len([i for i in items if not i.name.startswith(".")])
                found_dirs.append(f"- {dirname}/: {description} ({count} items)")
            except PermissionError:
                found_dirs.append(f"- {dirname}/: {description}")
    
    if found_dirs:
        output_lines.append("Key Directories:")
        output_lines.extend(found_dirs)
        output_lines.append("")
    
    # Add navigation tip
    output_lines.append("Tip: Use absolute paths for workspace files: /home/bob/alice/...")
    
    return Message("system", "\n".join(output_lines))


tool = ToolSpec(
    name="workspace",
    desc="Show workspace structure and key files",
    instructions=instructions,
    examples=examples,
    execute=execute_workspace,
    block_types=["workspace"],
)

__doc__ = tool.get_doc(__doc__)
