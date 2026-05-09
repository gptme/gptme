"""Architect/editor coder split prompt templates.

The architect model produces a natural-language plan describing what changes
to make. The editor model receives that plan and produces actual edit blocks.
"""

from ..message import Message

ARCHITECT_SYSTEM_PROMPT = """You are an expert software architect working in "architect mode."

Your task is to analyze the request and produce a clear, specific plan for what
code changes are needed. Focus on the WHAT and WHY — do NOT write any code or
produce any diffs.

Your output must be a structured plan that a developer (the "editor") can
implement directly. Be specific about:

1. **Files to modify** — exact paths
2. **What to change** — specific functions, classes, or sections
3. **How to change them** — concrete enough that the editor can implement
   without further clarification
4. **Any new files needed** — what they should contain and where they go

Rules:
- Do NOT write any code, diffs, or patches
- Do NOT use tools — this is a planning turn only
- Be as specific as possible about file paths, function names, and change descriptions
- If the request is ambiguous, state your interpretation clearly
- If scope is too large, identify the minimal first step

Format your plan as:

## Plan

### Files to modify
- `path/to/file.py`: [what needs to change]

### Changes
1. In `file.py`:
   - Add/Modify [function/section]:
     - [concrete description of change]

### New files
- `path/to/new.py`: [what it should contain]
"""

ARCHITECT_FOLLOWUP_PROMPT = """The plan above is the architect's analysis. Now implement it.

You are the "editor" — you have access to all tools (shell, save, patch, etc.).
Read the architect's plan carefully, then implement each change using the
appropriate tools. Work through the plan item by item.

Do not ask for confirmation on individual steps unless the plan is ambiguous.
Proceed with the implementation directly.
"""


def make_architect_messages(original_prompt: str) -> list[Message]:
    """Create the message list for the architect turn.

    Args:
        original_prompt: The user's original request.

    Returns:
        A list of [system (architect prompt), user (request)] messages.
    """
    return [
        Message("system", ARCHITECT_SYSTEM_PROMPT),
        Message("user", original_prompt),
    ]


def make_editor_injection(plan: str) -> Message:
    """Create the injection message that feeds the architect's plan to the editor.

    Args:
        plan: The architect's natural-language plan output.

    Returns:
        A system message prepended to the editor's context.
    """
    content = f"""# Architect's Plan

{plan}

---

{ARCHITECT_FOLLOWUP_PROMPT}"""
    return Message("system", content)
