"""Subagent tool — spawn, monitor, and coordinate child agents.

Extracted from a single 1100-line module into a package for maintainability.

Package structure:
- types.py      — Data classes and module-level state (Subagent, ReturnType, etc.)
- hooks.py      — Completion notification system (LOOP_CONTINUE hook)
- api.py        — Public API (subagent, subagent_status, subagent_wait, etc.)
- batch.py      — Batch execution (BatchJob, subagent_batch)
- execution.py  — Execution backends (thread, subprocess, process monitoring)
"""

# Re-export public API for backward compatibility
# Re-export ToolUse for examples()
from ..base import ToolSpec, ToolUse
from .api import subagent, subagent_read_log, subagent_status, subagent_wait
from .batch import BatchJob, subagent_batch
from .hooks import (
    _get_complete_instruction,
    _subagent_completion_hook,
    notify_completion,
)
from .types import (
    ReturnType,
    Status,
    Subagent,
    SubtaskDef,
    _completion_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)


def examples(tool_format):
    return f"""
### Executor Mode (single task)
User: compute fib 13 using a subagent
Assistant: Starting a subagent to compute the 13th Fibonacci number.
{
        ToolUse(
            "ipython", [], 'subagent("fib-13", "compute the 13th Fibonacci number")'
        ).to_output(tool_format)
    }
System: Subagent started successfully.
Assistant: Now we need to wait for the subagent to finish the task.
{ToolUse("ipython", [], 'subagent_wait("fib-13")').to_output(tool_format)}
System: {{"status": "success", "result": "The 13th Fibonacci number is 233"}}.

### Planner Mode (multi-task delegation)
User: implement feature X with tests
Assistant: I'll use planner mode to delegate implementation and testing to separate subagents.
{
        ToolUse(
            "ipython",
            [],
            '''subtasks = [
    {{"id": "implement", "description": "Write implementation for feature X"}},
    {{"id": "test", "description": "Write comprehensive tests"}},
]
subagent("feature-planner", "Feature X adds new functionality", mode="planner", subtasks=subtasks)''',
        ).to_output(tool_format)
    }
System: Planner spawned 2 executor subagents.
Assistant: Now I'll wait for both subtasks to complete.
{
        ToolUse("ipython", [], 'subagent_wait("feature-planner-implement")').to_output(
            tool_format
        )
    }
System: {{"status": "success", "result": "Implementation complete in feature_x.py"}}.
{ToolUse("ipython", [], 'subagent_wait("feature-planner-test")').to_output(tool_format)}
System: {{"status": "success", "result": "Tests complete in test_feature_x.py, all passing"}}.

### Context Modes

#### Full Context (default)
User: analyze this codebase
Assistant: I'll use full context mode for comprehensive analysis.
{
        ToolUse(
            "ipython",
            [],
            'subagent("analyze", "Analyze code quality and suggest improvements", context_mode="full")',
        ).to_output(tool_format)
    }

#### Selective Context (choose specific components)
User: write tests using pytest
Assistant: I'll use selective mode to share only project files, not context_cmd output.
{
        ToolUse(
            "ipython",
            [],
            'subagent("tests", "Write pytest tests for the calculate function", context_mode="selective", context_include=["files"])',
        ).to_output(tool_format)
    }

### Subprocess Mode (output isolation)
User: run a subagent without output mixing with parent
Assistant: I'll use subprocess mode for better output isolation.
{
        ToolUse(
            "ipython",
            [],
            'subagent("isolated", "Compute complex calculation", use_subprocess=True)',
        ).to_output(tool_format)
    }
System: Subagent started in subprocess mode.

### ACP Mode (multi-harness support)
User: delegate this task to a Claude Code agent
Assistant: I'll use ACP mode to run this via a different agent harness.
{
        ToolUse(
            "ipython",
            [],
            'subagent("claude-task", "Analyze and refactor the auth module", use_acp=True, acp_command="claude-code-acp")',
        ).to_output(tool_format)
    }
System: Started subagent "claude-task" in ACP mode.

### Batch Execution (parallel tasks)
User: implement, test, and document a feature in parallel
Assistant: I'll use subagent_batch for parallel execution with fire-and-gather pattern.
{
        ToolUse(
            "ipython",
            [],
            '''job = subagent_batch([
    ("impl", "Implement the user authentication feature"),
    ("test", "Write tests for authentication"),
    ("docs", "Document the authentication API"),
])
# Do other work while subagents run...
results = job.wait_all(timeout=300)
for agent_id, result in results.items():
    print(f"{{agent_id}}: {{result['status']}}")''',
        ).to_output(tool_format)
    }
System: Started batch of 3 subagents: ['impl', 'test', 'docs']
impl: success
test: success
docs: success

### Fire-and-Forget with Hook Notifications
User: start a subagent and continue working
Assistant: I'll spawn a subagent. Completion will be delivered via the LOOP_CONTINUE hook.
{
        ToolUse(
            "ipython",
            [],
            '''subagent("compute-demo", "Compute pi to 100 digits")
# I can continue with other work now
# When the subagent completes, I'll receive a system message like:
# "✅ Subagent 'compute-demo' completed: pi = 3.14159..."''',
        ).to_output(tool_format)
    }
System: Started subagent "compute-demo"
System: ✅ Subagent 'compute-demo' completed: pi = 3.14159265358979...

### Profile-Based Subagents (auto-detected from agent_id)
User: explore this codebase and summarize the architecture
Assistant: I'll use the explorer profile for a read-only analysis.
{
        ToolUse(
            "ipython",
            [],
            'subagent("explorer", "Analyze the codebase architecture and summarize key patterns")',
        ).to_output(tool_format)
    }
System: Subagent started successfully.

### Profile with Model Override
User: research best practices for error handling
Assistant: I'll spawn a researcher subagent with a faster model for web research.
{
        ToolUse(
            "ipython",
            [],
            'subagent("researcher", "Research error handling best practices in Python", model="openai/gpt-4o-mini")',
        ).to_output(tool_format)
    }
System: Subagent started successfully.

### Structured Delegation Template
User: implement a robust auth feature
Assistant: I'll use the structured delegation template for clear task handoff.
{
        ToolUse(
            "ipython",
            [],
            'subagent("auth-impl", "TASK: Implement JWT auth | OUTCOME: auth.py with tests | MUST: bcrypt, validation | MUST NOT: plaintext passwords")',
        ).to_output(tool_format)
    }
System: Subagent started successfully.

### Isolated Subagent (Worktree)
User: implement a feature without affecting my working directory
Assistant: I'll run the subagent in an isolated git worktree so it won't modify your files.
{
        ToolUse(
            "ipython",
            [],
            'subagent("feature-impl", "Implement the new caching layer in cache.py", isolated=True)',
        ).to_output(tool_format)
    }
System: Subagent started successfully.
""".strip()


instructions = """
You can create, check status, wait for, and read logs from subagents.

Subagents support a "fire-and-forget-then-get-alerted" pattern:
- Call subagent() to start an async task (returns immediately)
- Continue with other work
- Receive completion messages via the LOOP_CONTINUE hook
- Optionally use subagent_wait() for explicit synchronization

Key features:
- Agent profiles: Use profile names as agent_id for automatic profile detection
- model="provider/model": Override parent's model (route cheap tasks to faster models)
- use_subprocess=True: Run subagent in subprocess for output isolation
- use_acp=True: Run subagent via ACP protocol (supports any ACP-compatible agent)
- acp_command="claude-code-acp": Use a different ACP agent (default: gptme-acp)
- isolated=True: Run subagent in a git worktree for filesystem isolation
- subagent_batch(): Start multiple subagents in parallel
- Hook-based notifications: Completions delivered as system messages

## Agent Profiles for Subagents

Use profiles to create specialized subagents with appropriate capabilities.
When agent_id matches a profile name, the profile is auto-applied:
- explorer: Read-only analysis (tools: read)
- researcher: Web research without file modification (tools: browser, read)
- developer: Full development capabilities (all tools)
- isolated: Restricted processing for untrusted content (tools: read, ipython)
- computer-use: Visual UI testing specialist (tools: computer, vision, ipython, shell)
- browser-use: Web interaction and testing specialist (tools: browser, screenshot, vision, shell) — supports interactive browsing (open_page, click, fill, scroll) and one-shot reads

Example: `subagent("explorer", "Explore codebase")`
With model override: `subagent("researcher", "Find docs", model="openai/gpt-4o-mini")`
Computer-use example: `subagent("computer-use", "Click the Submit button, wait for the modal, and screenshot the result")`
Browser-use example: `subagent("browser-use", "Open localhost:5173, fill the chat input, click send, and report the result")`

Use subagent_read_log() to inspect a subagent's conversation log for debugging.

## Structured Delegation Template

For complex delegations, use this 7-section template for clear task handoff:

TASK: [What the subagent should do]
EXPECTED OUTCOME: [Specific deliverable - format, structure, quality bars]
REQUIRED SKILLS: [What capabilities the subagent needs]
REQUIRED TOOLS: [Specific tools the subagent should use]
MUST DO: [Non-negotiable requirements]
MUST NOT DO: [Explicit constraints and forbidden actions]
CONTEXT: [Background info, dependencies, related work]

Example prompt using the template:
'''
TASK: Implement the user authentication feature
EXPECTED OUTCOME: auth.py with login/logout endpoints, passing tests
REQUIRED SKILLS: Python, FastAPI, JWT tokens
REQUIRED TOOLS: save, shell (for pytest)
MUST DO: Use bcrypt for password hashing, return proper HTTP status codes
MUST NOT DO: Store plaintext passwords, skip input validation
CONTEXT: This is for the gptme server API, see existing endpoints in server.py
'''
""".strip()

tool = ToolSpec(
    name="subagent",
    desc="Create and manage subagents",
    instructions=instructions,
    examples=examples,
    functions=[
        subagent,
        subagent_status,
        subagent_wait,
        subagent_read_log,
        subagent_batch,
    ],
    disabled_by_default=True,
    hooks={
        "completion": (
            "loop.continue",  # HookType.LOOP_CONTINUE.value
            _subagent_completion_hook,
            50,  # High priority to ensure timely delivery
        )
    },
)
__doc__ = tool.get_doc(__doc__)

__all__ = [
    # Public API
    "subagent",
    "subagent_status",
    "subagent_wait",
    "subagent_read_log",
    "subagent_batch",
    "BatchJob",
    # Types
    "SubtaskDef",
    "ReturnType",
    "Subagent",
    "Status",
    # Hooks
    "notify_completion",
    "_subagent_completion_hook",
    "_get_complete_instruction",
    # Module-level state (re-exported for backward compatibility)
    "_subagents",
    "_subagents_lock",
    "_subagent_results",
    "_subagent_results_lock",
    "_completion_queue",
    # Tool registration
    "tool",
]
