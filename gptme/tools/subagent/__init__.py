"""Subagent tool — spawn, monitor, and coordinate child agents.

Extracted from a single 1100-line module into a package for maintainability.

Package structure:
- types.py      — Data classes and module-level state (Subagent, ReturnType, etc.)
- hooks.py      — Completion notification system (LOOP_CONTINUE hook)
- api.py        — Public API (subagent, subagent_status, subagent_wait, etc.)
- batch.py      — Batch execution (BatchJob, subagent_batch, subagent_parallel, subagent_pipeline)
- execution.py  — Execution backends (thread, subprocess, process monitoring)
"""

# Re-export public API for backward compatibility
# Re-export ToolUse for examples()
from ..base import ToolFunction, ToolSpec, ToolUse
from .api import (
    subagent,
    subagent_cancel,
    subagent_list,
    subagent_read_log,
    subagent_reply,
    subagent_status,
    subagent_wait,
    subagent_wait_any,
)
from .batch import BatchJob, subagent_batch, subagent_parallel, subagent_pipeline
from .execution import get_current_agent_id
from .hooks import (
    _get_complete_instruction,
    _subagent_completion_hook,
    notify_completion,
    notify_progress,
)
from .types import (
    ReturnType,
    Status,
    Subagent,
    SubtaskDef,
    _completion_queue,
    _progress_queue,
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
Assistant: I'll use subprocess mode so selective context can include workspace files without inheriting the full parent context.
{
        ToolUse(
            "ipython",
            [],
            'subagent("tests", "Write pytest tests for the calculate function", context_mode="selective", context_include=["workspace"], use_subprocess=True)',
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

### Workspace-Aware Subagent (explicit workdir)
User: I just cd'd into /path/to/project which has a gptme.toml — spawn a subagent there
Assistant: I'll use workdir to make the subagent operate in that workspace and load its config.
{
        ToolUse(
            "ipython",
            [],
            'subagent("project", "Add feature X", workdir="/path/to/project", use_subprocess=True)',
        ).to_output(tool_format)
    }
System: Subagent started in subprocess mode (workdir=/path/to/project).

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

### List Subagents (observability)
User: what subagents are currently running?
Assistant: I'll use subagent_list to check running agents.
{
        ToolUse(
            "ipython",
            [],
            "subagent_list()",
        ).to_output(tool_format)
    }
System: Listing 2 subagents::
  - analyze (running, 42s) -- "Analyze the codebase architecture..."
  - fib-13 (success, 120s) -- "compute the 13th Fibonacci number"

### Parallel Fan-out (wait for all, ordered results)
User: implement, test, and document a feature in parallel and collect all results
Assistant: I'll use subagent_parallel to fan out all tasks and wait for them together.
{
        ToolUse(
            "ipython",
            [],
            '''tasks = [
    ("impl", "Implement the user authentication feature"),
    ("test", "Write tests for authentication"),
    ("docs", "Document the authentication API"),
]
results = subagent_parallel(tasks, timeout=300)
for (agent_id, _), result in zip(tasks, results):
    print(f"{{agent_id}}: {{result['status']}} — {{result['result'][:60]}}")''',
        ).to_output(tool_format)
    }
System: impl: success — Authentication feature implemented in auth.py
test: success — 12 tests added, all passing
docs: success — API documented in docs/auth.md

### Pipeline (multi-stage fan-out, no barrier between stages)
User: review these files in two stages — first find issues, then verify each finding
Assistant: I'll use subagent_pipeline so file B's review starts while file A's verification is running.
{
        ToolUse(
            "ipython",
            [],
            '''results = subagent_pipeline(
    [("auth", "Review auth.py for bugs"), ("db", "Review db.py for bugs")],
    # Stage 0: review each file
    lambda item, _: item,
    # Stage 1: adversarially verify the review findings
    lambda item, prev: "Verify these findings, keep only real bugs: " + prev,
    timeout=300,
)
# auth advances to stage 1 as soon as its stage 0 finishes,
# while db may still be in stage 0.
for (prefix, _), stage_results in zip([("auth", ...), ("db", ...)], results):
    print(f"{prefix}: {stage_results[-1]['status']}")''',
        ).to_output(tool_format)
    }
System: auth-s0 done → auth-s1 started; db-s0 done → db-s1 started
System: auth: success, db: success

### Batch Execution (fire-and-forget with explicit sync)
User: start tasks in background and continue working
Assistant: I'll use subagent_batch to start tasks in the background. Completion hooks will notify me.
{
        ToolUse(
            "ipython",
            [],
            '''job = subagent_batch([
    ("impl", "Implement the user authentication feature"),
    ("test", "Write tests for authentication"),
])
# Do other work while subagents run — hook notifications arrive automatically:
# "✅ Subagent 'impl' completed: ..."
# Or explicitly wait for all when needed:
results = job.wait_all(timeout=300)''',
        ).to_output(tool_format)
    }
System: Started batch of 2 subagents: ['impl', 'test']

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

### Context-Isolated Subagent (no workspace context)
User: verify this output without exposing our workspace secrets to the subagent
Assistant: I'll use context_window=0 so the subagent only sees what I explicitly give it in the prompt, with no workspace files or secrets inherited.
{
        ToolUse(
            "ipython",
            [],
            'subagent("verifier", "Check that the output file has no syntax errors", context_window=0)',
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
- isolation="worktree": Run subagent in a git worktree for filesystem isolation (preferred over isolated=True)
- workdir="/path/to/dir": Set the working directory for the subagent (defaults to cwd)
- redact_secrets=True (default): Redact API keys, tokens, and passwords from workspace context
- context_window=0: Minimal context — only agent identity + tools, no workspace files (strongest isolation)
- context_window=N: Limit workspace context to at most N messages
- subagent_parallel(tasks, timeout): Fan out N subagents and wait for all — returns ordered list of results
- subagent_pipeline(items, *stages, timeout): Multi-stage fan-out with no barrier between stages — item A advances to stage 2 while item B is still in stage 1; each stage callable receives (item_prompt, prev_result) and returns the next stage's prompt
- subagent_batch(): Start multiple subagents and return a BatchJob for explicit synchronization
- subagent_cancel(): Cancel a running subagent (SIGTERM for subprocess, marks result for threads)
- subagent_wait_any(agent_ids, timeout): Wait for the first of N subagents to complete — returns (agent_id, result). Useful for race/hedging patterns.
- subagent_reply(agent_id, reply): Answer a clarification request and re-spawn the subagent
- Hook-based notifications: Completions (and clarification requests) delivered as system messages

## Context Isolation

Subagents do NOT inherit the parent's conversation history — they always start
with a fresh context. What subagents DO inherit (in context_mode="full"):

- Workspace files listed in gptme.toml [prompt] files (e.g. AGENTS.md, README)
- Dynamic context_cmd output (if configured in gptme.toml)
- User-level config files from ~/.config/gptme

This means secrets stored in workspace config files or produced by context_cmd
can reach the subagent. Secret patterns (API_KEY, TOKEN, PASSWORD, etc.) are
redacted by default (redact_secrets=True). Pass redact_secrets=False to disable
if legitimate config values are incorrectly redacted.

### Controlling context depth with context_window

Limit how much workspace context flows to the subagent. Reach for these when
you need tighter control over what the subagent sees:

- `context_window=None` (default): The subagent sees your full workspace (files,
  tools, recent conversation). Best for tasks that benefit from maximum awareness.
- `context_window=0`: **Strongest isolation** — the subagent gets only agent
  identity and tool descriptions, no workspace files or context_cmd output. Use
  this when the subagent handles sensitive data (secrets, prompts) that should
  not leak into verification or analysis tasks. The subagent knows only what
  you explicitly tell it in the task prompt.
- `context_window=N`: Limits workspace to at most N context messages. Useful when
  the default is too bloated but you still want the subagent to see some workspace
  history — trim without fully isolating.

`context_window=0` is equivalent to `context_mode="selective", context_include=["agent", "tools"]`
but is a simpler one-parameter alternative. Only applies to thread-mode subagents.

## Agent Profiles for Subagents

Use profiles to create specialized subagents with appropriate capabilities.
When agent_id matches a profile name, the profile is auto-applied:
- explorer: Read-only analysis (tools: read)
- researcher: Web research without file modification (tools: browser, read)
- developer: Full development capabilities (all tools)
- verifier: Critical review & validation (tools: read, shell, ipython, chats)
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

## Clarification Requests

When a subagent ends with a ``clarify`` block, it signals that it needs more
information from the parent before it can continue:

```clarify
Which output format should I use: JSON or CSV?
```

The parent receives a hook notification:
  ❓ Subagent 'X' needs clarification: Which output format should I use: JSON or CSV?
  Call subagent_reply('X', '<your answer>') to continue.

Use ``subagent_reply(agent_id, reply)`` to answer and re-spawn the subagent.
The re-spawned subagent receives the original prompt plus the Q&A so it can
complete the task without losing context.
""".strip()

tool = ToolSpec(
    name="subagent",
    desc="Create and manage subagents",
    instructions=instructions,
    examples=examples,
    functions=[
        ToolFunction.from_callable(f)
        for f in [
            subagent,
            subagent_cancel,
            subagent_list,
            subagent_reply,
            subagent_status,
            subagent_wait,
            subagent_wait_any,
            subagent_read_log,
            subagent_batch,
            subagent_parallel,
            subagent_pipeline,
        ]
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
    "subagent_cancel",
    "subagent_list",
    "subagent_reply",
    "subagent_status",
    "subagent_wait",
    "subagent_wait_any",
    "subagent_read_log",
    "subagent_batch",
    "subagent_parallel",
    "subagent_pipeline",
    "BatchJob",
    # Types
    "SubtaskDef",
    "ReturnType",
    "Subagent",
    "Status",
    # Hooks
    "notify_completion",
    "notify_progress",
    "_subagent_completion_hook",
    "_get_complete_instruction",
    # Execution context
    "get_current_agent_id",
    # Module-level state (re-exported for backward compatibility)
    "_subagents",
    "_subagents_lock",
    "_subagent_results",
    "_subagent_results_lock",
    "_completion_queue",
    "_progress_queue",
    # Tool registration
    "tool",
]
