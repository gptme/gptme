:audience: developer

Hooks
=====

.. note::
   This is a new feature added in response to `issue #156 <https://github.com/gptme/gptme/issues/156>`_.

The hook system allows tools and plugins to register callbacks that execute at various points in gptme's lifecycle. This enables powerful extensions like automatic linting, memory management, pre-commit checks, and more.

Hook Types
----------

The following hook types are available:

Turn And Step Lifecycle Hooks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``TURN_PRE``: Once per submitted user prompt, before the turn starts
- ``STEP_PRE``: Before each generation/tool-execution step within a turn
- ``STEP_POST``: After each step completes
- ``TURN_POST``: After the whole turn completes
- ``MESSAGE_TRANSFORM``: Rewrite assistant message content before persistence/display

``TURN_PRE`` is the clean "user prompt submitted" surface. ``STEP_PRE`` may run
multiple times inside one turn if the assistant keeps calling tools.

Tool Lifecycle Hooks
~~~~~~~~~~~~~~~~~~~~

- ``TOOL_EXECUTE_PRE``: Before executing any tool
- ``TOOL_EXECUTE_POST``: After executing any tool
- ``TOOL_TRANSFORM``: Transform tool execution
- ``TOOL_CONFIRM``: Blocking confirmation/deny decision before execution
  (see :ref:`confirmation-hooks-and-deny-decisions`)

File Operation Hooks
~~~~~~~~~~~~~~~~~~~~~

- ``FILE_SAVE_PRE``: Before saving or appending to a file
- ``FILE_SAVE_POST``: After successfully saving or appending to a file
- ``FILE_PATCH_PRE``: Before patching a file
- ``FILE_PATCH_POST``: After patching a file

For ``save`` and ``append``, PRE receives the requested ``content`` before
newline normalization. POST receives the content passed to the text writer,
before platform/file newline translation. For ``append`` this is **only the
appended chunk**, including any inserted leading separator and final newline,
not the resulting full file. Read ``path`` to inspect the full file after a write.
Both events receive the resolved absolute ``path``; POST also receives ``created``.
For append, ``created`` records whether the destination was absent before its
creation prompts and hooks. Hook messages are propagated to the conversation.

Append dispatches PRE after confirmation but before creating or writing the
file, and POST only after a successful write and close. Refused or failed writes
do not emit POST. These are notification hooks, not a veto, rollback, or
transaction API; hook failures retain the registry's normal error handling.

Session Lifecycle Hooks
~~~~~~~~~~~~~~~~~~~~~~~~

- ``SESSION_START``: At session start
- ``SESSION_END``: At session end

Global and project configuration can attach bounded shell commands to these two
lifecycle events through ``[[hooks.scripts]]``. The lists are additive and run
in descending ``priority`` order; global hooks win equal-priority ties, then
declaration order is preserved. See :ref:`global-config` and
:ref:`project-config`. Other hook types carry structured inputs or control-flow
semantics and are not exposed through the shell adapter.

Generation Hooks
~~~~~~~~~~~~~~~~

- ``GENERATION_PRE``: Before generating response
- ``GENERATION_POST``: After generating response
- ``GENERATION_INTERRUPT``: Interrupt generation

Usage
-----

Registering Hooks from Tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tools can register hooks in their ``ToolSpec`` definition:

.. code-block:: python

   from gptme.tools.base import ToolSpec
   from gptme.hooks import HookType
   from gptme.message import Message

   def on_file_save(path, content, created):
       """Hook function called after a file is saved."""
       if path.suffix == ".py":
           # Run linting on Python files
           return Message("system", f"Linted {path}")
       return None

   tool = ToolSpec(
       name="linter",
       desc="Automatic linting tool",
       hooks={
            "file_save": (
               HookType.FILE_SAVE_POST.value,  # Hook type
               on_file_save,                    # Hook function
               10                               # Priority (higher = runs first)
           )
       }
   )

Registering Hooks Programmatically
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can also register hooks directly:

.. code-block:: python

   from gptme.hooks import register_hook, HookType
   from gptme.hooks.confirm import ConfirmationResult

   def my_hook_function(manager):
       """Custom hook function."""
       # Do something
       return Message("system", "Hook executed!")

   register_hook(
       name="my_custom_hook",
       hook_type=HookType.TURN_PRE,
       func=my_hook_function,
       priority=0,
       enabled=True
   )

Hook Function Signatures
~~~~~~~~~~~~~~~~~~~~~~~~~

Hook functions receive different arguments depending on the hook type:

.. code-block:: python

   # Turn/step hooks
   def turn_hook(manager):
       pass

   # Tool hooks
   def tool_hook(log, workspace, tool_use):
       pass

   # File hooks
   def file_hook(log, workspace, path, content, created=False):
       pass

   # Session hooks
   def session_hook(logdir, workspace, initial_msgs):
       pass

   # Confirmation hooks (see "Confirmation Hooks and Deny Decisions" below)
   def confirm_hook(tool_use, preview=None, workspace=None):
       if is_dangerous(tool_use):
           return ConfirmationResult.skip("Blocked: destructive path")
       return None  # fall through to the next confirmation hook

Most hook functions can:

- Return ``None`` (no action)
- Return a single ``Message`` object
- Return a generator that yields ``Message`` objects
- Raise exceptions (which are caught and logged)

``TOOL_CONFIRM`` hooks are the exception: they *return* a
``ConfirmationResult`` rather than yielding messages. See below.

.. _confirmation-hooks-and-deny-decisions:

Confirmation Hooks and Deny Decisions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``TOOL_CONFIRM`` is the hook for deterministic, below-the-model guardrails: it
runs before a tool executes and can **deny** execution outright.

.. code-block:: python

   from gptme.hooks import HookType, register_hook
   from gptme.hooks.confirm import ConfirmationResult

   def block_secret_reads(tool_use, preview=None, workspace=None):
       """Deny any shell command that reads private keys."""
       if tool_use.tool == "shell" and "id_rsa" in (tool_use.content or ""):
           return ConfirmationResult.skip("Blocked: secret file access")
       return None  # not our decision — fall through

   def register():
       register_hook(
           "guardrail.secrets",
           HookType.TOOL_CONFIRM,
           block_secret_reads,
           priority=1000,  # must exceed every built-in confirmation hook
       )

The three results a confirmation hook can return:

- ``ConfirmationResult.skip(message)`` — **deny**; the tool does not execute, and ``message`` is surfaced as the reason.
- ``ConfirmationResult.confirm()`` — approve without prompting the user.
- ``ConfirmationResult.edit(content)`` — execute with modified content.

Returning ``None`` falls through to the next ``TOOL_CONFIRM`` hook in priority
order; the first non-``None`` result wins. If no hook is registered at all,
execution is auto-confirmed (autonomous mode).

.. warning::

   A guardrail must out-rank **every** built-in confirmation hook, or it can be
   pre-empted before it ever runs. The built-ins register at:

   - ``server_confirm`` — priority **100**
   - ``cli_confirm`` — priority **0**
   - ``auto_confirm`` — priority **0**

   Hooks sort by ``(priority, name)`` **descending**, so equal priority is broken
   by name in *reverse* alphabetical order — not by registration order. A
   guardrail named ``guardrail.secrets`` registered at priority ``100`` therefore
   loses to ``server_confirm`` (``"s" > "g"``), which returns a non-``None``
   result and prevents the guardrail from running at all. Pick a priority
   strictly greater than 100 (the example uses ``1000``) rather than relying on
   the name tie-break.

.. note::

   ``--no-confirm``/``-y`` only stops gptme from registering its *interactive*
   confirmation hooks (``cli_confirm``/``server_confirm``). A plugin-registered
   ``TOOL_CONFIRM`` hook is still called, so a ``skip`` result keeps blocking
   execution in autonomous runs. This is what makes the hook usable as a real
   guardrail rather than a prompt.

Built-in Credential-Output Policy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The built-in ``guardrails`` confirmation hook (priority 200) also recognizes
obvious credential-output shell commands. Like its existing destructive-command
and secret-path rules, it follows ``GPTME_GUARDRAILS``: ``shadow`` (default) logs
and allows, ``enforce`` skips execution, and ``off`` disables the hook. Merely
installing this detector does **not** enable enforcement. The hook must also
be registered and allowed by the runtime's hook configuration.

Covered literal syntax includes shell xtrace (``bash -eux``, ``sh -x``,
``set -x``, ``set -o xtrace``, ``BASH_XTRACEFD`` assignments), environment dumps
(``env``, ``printenv``), variable-state dumps (bare ``set``, ``export``/``declare``/
``typeset`` and their ``-p`` forms), and sensitive variable expansions printed
by ``echo``/``printf``. Sensitive names have a KEY, TOKEN, SECRET, PASSWORD,
PASSWD or CREDENTIAL(S) underscore-delimited component. Simple ``env``,
``command``, ``exec`` and option-free ``sudo`` wrappers, shell ``-c`` strings,
command substitutions and shell-fed heredocs are inspected. Shell options
are inspected only before the script/command argument, not script arguments.
Refusal reasons and shadow logs contain a fixed description, never matched
source text or variable values.

Benign forms include ``env NAME=value command``, ``env -i ... command``,
``set +x``, name-only ``compgen -e``, literal quoted documentation and heredoc
data not used as shell code. ``printenv NAME ...`` is allowed when every
operand is a literal variable name that does not look secret (the same
KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL name rule as ``echo "$NAME"``); bare
``printenv`` and dynamic operands stay blocked. ``-0``/``--null`` and the
``--`` option separator are supported; unknown options are denied rather than
silently discarded. Prefer ``printenv SESSION_LABEL`` or ``echo "$SESSION_LABEL"``
to dumping the environment and filtering it. Variable length
and literal presence diagnostics (``${#API_KEY}``, ``${API_KEY:+configured}``)
are allowed. Using a credential as a process argument is distinct from printing
it; existing secret-path rules still apply independently. Variable-name matching
is a heuristic, not a guarantee: names such as ``DATABASE_URL`` or ``GH_PAT``
can carry credentials without matching this rule. Only print values you know
are diagnostic; use ``compgen -e`` when you need names alone.

Secret paths reached indirectly
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The secret-path rule also inspects pathname globs in literal reader commands
(``cat``, ``head``, ``tail``, ``less``, ``more``, ``sed``, ``awk`` and grep variants).
For example, ``cat config/*`` is flagged if it expands to a protected ``.pem``
file. Stdin file redirections on those readers are included
(``cat < config/*``); output redirections and non-stdin descriptors are not.
Quoted and escaped wildcard characters remain literal, including ``$`` and
``~`` inside single quotes. Unquoted and double-quoted parameter and unquoted
tilde expansion still apply before glob matching. Unquoted parameter values
are split on default shell whitespace (space, tab and LF); quoted whitespace
stays within the pathname. Custom ``IFS`` and shell-local assignments are not
modeled. The same unquoted/double-quoted parameter expansion applies to grep
search patterns (``grep -rF "$NEEDLE"``); single-quoted patterns stay literal.
Unset or empty names expand to empty, so ``grep -rF "$UNSET" .`` is an empty
fixed-string search. Unquoted empty names are removed as words. Unquoted
pattern expansions also field-split, so ``NEEDLE='TOKEN .'`` makes
``grep -rF $NEEDLE`` search for ``TOKEN`` in ``.``. GNU grep's
``-r``/``-R`` and ``--recursive`` searches inspect protected files in the search
root and its immediate child directories, including wildcard directory operands.
Literal recursive roots retain shell quoting instead of expanding quoted ``$``
or ``~`` a second time. ``-R``/``--dereference-recursive`` follows file symlinks
with protected names; ``-r`` skips discovered symlinks. Directory symlinks
remain outside the bounded traversal for both modes.
``--directories=recurse`` has the same effect; a later ``skip`` or ``read``
disables it. Ordinary ``rg`` recursion is not added: hidden/ignore rules differ
from grep.

A simple grep pattern must match a non-comment assignment line in dotenv files
(or any line in other protected file formats). This leaves searches such as
``grep -rn 'def foo' .`` alone while detecting ``grep -rn API_KEY .`` when a
matching assignment is in reach. Include/exclude filters, including
``--exclude-from``, fixed strings, case-insensitive, word and whole-line matching
are recognized. Ripgrep ``--no-fixed-strings`` last-wins against
``-F``/``--fixed-strings`` and restores regex matching. GNU grep ``-z``/``--null-data`` treats ``.`` as matching an embedded LF
inside the NUL-delimited record; ripgrep ``--null-data`` does not.
Ripgrep ``--crlf``
(last-wins against ``--no-crlf``) treats a
trailing carriage return as part of the line ending for whole-line matches;
GNU grep stays LF-only. Ripgrep ``-E``/``--encoding`` and ``-r``/``--replace``
operands are consumed without replacing the search pattern; candidate decoding
remains the default text decoding, not a simulation of every ripgrep encoding.
Ripgrep ``--passthru``/``--passthrough`` prints matching and non-matching
lines, so a reached protected file is flagged even when the pattern does not
match. Context searches
are conservative; quiet/count/filename-only searches do not add reach refusals.
Complex regular expressions are conservatively flagged when a protected file
is reached, rather than evaluated with an unbounded backtracking engine.

Reach checking shares a 256-directory-entry budget across the command and reads
at most 64 KiB of each candidate. It does not walk deep trees, follow directory
symlinks during recursive discovery, track shell ``cd`` commands, execute scripts,
or resolve innocuously named symlinks to their targets. Exhausted budgets and
unreadable files can leave misses. The existing literal-path rule is unchanged,
including its conservative treatment of explicitly named protected paths.
Refusals and shadow logs report paths only, never candidate file contents.

This is a bounded accidental-disclosure policy, **not a shell sandbox**.
It does not resolve dynamic command names, arbitrary interpreters, sourced
files, script contents, ``eval``, all wrapper options or obfuscated expansions.
Function definitions and unreachable branches are scanned conservatively.
Pipelines following a dump are still blocked, even with an anchored non-secret
name filter: a multiline value from another variable can contain a continuation
line matching that filter. A downstream filter also does not prevent tracing or
intermediate output from exposing a credential.
Do not infer new-pattern false-positive rates from older shadow logs; evaluate
fresh exposure before enabling the entire hook in unattended sessions.

Managing Hooks
--------------

Query Hooks
~~~~~~~~~~~

.. code-block:: python

   from gptme.hooks import get_hooks, HookType

   # Get all hooks
   all_hooks = get_hooks()

   # Get hooks of a specific type
   tool_hooks = get_hooks(HookType.TOOL_EXECUTE_POST)

Enable/Disable Hooks
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from gptme.hooks import enable_hook, disable_hook

   # Disable a hook
   disable_hook("linter.file_save")

   # Re-enable it
   enable_hook("linter.file_save")

Unregister Hooks
~~~~~~~~~~~~~~~~

.. code-block:: python

   from gptme.hooks import unregister_hook, HookType

   # Unregister from specific type
   unregister_hook("my_hook", HookType.FILE_SAVE_POST)

   # Unregister from all types
   unregister_hook("my_hook")

Examples
--------

Pre-commit Hook
~~~~~~~~~~~~~~~

Automatically run pre-commit checks after files are saved:

.. code-block:: python

   from pathlib import Path
   from gptme.tools.base import ToolSpec
   from gptme.hooks import HookType
   from gptme.message import Message
   import subprocess

   def run_precommit(path: Path, content: str, created: bool):
       """Run pre-commit on saved file."""
       try:
           result = subprocess.run(
               ["pre-commit", "run", "--files", str(path)],
               capture_output=True,
               text=True,
               timeout=30
           )
           if result.returncode != 0:
               yield Message("system", f"Pre-commit checks failed:\n{result.stdout}")
           else:
               yield Message("system", "Pre-commit checks passed", hide=True)
       except subprocess.TimeoutExpired:
           yield Message("system", "Pre-commit checks timed out", hide=True)

   tool = ToolSpec(
       name="precommit",
       desc="Automatic pre-commit checks",
       hooks={
           "precommit_check": (
               HookType.FILE_SAVE_POST.value,
               run_precommit,
               5  # Run after other hooks
           )
       }
   )

Memory/Context Hook
~~~~~~~~~~~~~~~~~~~

Automatically add context at session start:

.. code-block:: python

   def add_context(logdir, workspace, initial_msgs):
       """Add relevant context at session start."""
       context = load_relevant_context(workspace)
       if context:
           yield Message("system", f"Loaded context:\n{context}", pinned=True)

   tool = ToolSpec(
       name="memory",
       desc="Automatic context loading",
       hooks={
           "load_context": (
               HookType.SESSION_START.value,
               add_context,
               10
           )
       }
   )

Linting Hook
~~~~~~~~~~~~

Automatically lint files after saving:

.. code-block:: python

   def lint_file(path: Path, content: str, created: bool):
       """Lint Python files."""
       if path.suffix != ".py":
           return

       import subprocess
       result = subprocess.run(
           ["ruff", "check", str(path)],
           capture_output=True,
           text=True
       )

       if result.returncode != 0:
           yield Message("system", f"Linting issues:\n{result.stdout}")

   tool = ToolSpec(
       name="linter",
       desc="Automatic Python linting",
       hooks={
           "lint": (HookType.FILE_SAVE_POST.value, lint_file, 5)
       }
   )

Built-in Hooks
--------------

gptme ships with several built-in hooks that provide core functionality:

**Session & Context**

- ``active_context``: Selects relevant files to include before generation
- ``agents_md_inject``: Loads AGENTS.md/CLAUDE.md when the working directory changes,
  skipping files inside other agents' workspaces (see :ref:`agent-instruction-files`)
- ``cwd_tracking``: Tracks the current working directory across tool calls
- ``time_awareness``: Injects current time into context
- ``token_awareness``: Monitors token budget and warns when approaching limits
- ``cost_awareness``: Tracks and reports LLM API costs
- ``cache_awareness``: Surfaces cache hit rates for prompt caching

**Tool Confirmation**

- ``cli_confirm``: Terminal-based tool confirmation with preview
- ``auto_confirm``: Auto-approves tools in autonomous/non-interactive mode
- ``server_confirm``: Confirmation via WebUI/API for server mode

**User Input**

- ``elicitation``: Structured user input (forms, choices) in CLI
- ``server_elicit``: Elicitation via WebUI/API for server mode
- ``form_autodetect``: Detects when assistant output contains form-like choices

**Code Quality**

- ``markdown_validation``: Detects codeblock cut-offs in generated content

**Agent Awareness**

- ``workspace_agents``: Detects parallel agents (gptme, Claude Code, Codex, Goose, OpenCode, Amp) running in the same workspace

Best Practices
--------------

1. **Keep hooks fast**: Hooks run synchronously and can slow down operations
2. **Handle errors gracefully**: Use try-except to prevent hook failures from breaking the system
3. **Use priorities wisely**: Higher priority hooks run first (use for dependencies)
4. **Return Messages appropriately**: Use ``hide=True`` for verbose/debug messages
5. **Test hooks thoroughly**: Hooks run in the main execution path
6. **Document hook behavior**: Explain what your hooks do and when they run
7. **Consider disabling hooks**: Make hooks easy to disable via configuration

Thread Safety
-------------

The hook registry is thread-safe. Each thread maintains its own tool state, and hooks are registered per-thread.

When running in server mode with multiple workers, hooks must be registered in each worker process.

Configuration
-------------

Script hooks are declared in configuration, with their priority set per entry.
Global hooks go in ``~/.config/gptme/config.toml`` and project hooks in
``gptme.toml``; the lists are additive and run in descending ``priority`` order:

.. code-block:: toml

   [[hooks.scripts]]
   event = "session.end"
   command = "~/bin/save-agent-context"
   timeout = 30
   priority = 10

Hooks registered in Python are enabled and disabled at runtime through the
registry, not through environment variables:

.. code-block:: python

   from gptme.hooks import disable_hook, enable_hook

   disable_hook("precommit.precommit_check")

See :ref:`global-config` and :ref:`project-config` for the full configuration
reference.

Migration Guide
---------------

Converting Existing Features to Hooks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have features that should be hooks:

1. **Identify the appropriate hook type**: Choose from the available hook types
2. **Extract the logic**: Move the feature logic into a hook function
3. **Register the hook**: Add it to a ToolSpec or register programmatically
4. **Test thoroughly**: Ensure the hook works in all scenarios
5. **Update documentation**: Document the new hook

Example: Converting pre-commit checks to a hook
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Before** (hard-coded in chat.py):

.. code-block:: python

   # In chat.py
   if check_for_modifications(log):
       run_precommit_checks()

**After** (as a hook):

.. code-block:: python

   # In a tool
   def precommit_hook(log, workspace):
       if check_for_modifications(log):
           run_precommit_checks()

   tool = ToolSpec(
       name="precommit",
       hooks={
           "check": (HookType.TURN_POST.value, precommit_hook, 5)
       }
   )

API Reference
-------------

.. automodule:: gptme.hooks
   :members:
   :undoc-members:
   :show-inheritance:

See Also
--------

- :doc:`tools` - Tool system documentation
- :doc:`config` - Configuration options
- `Issue #156 <https://github.com/gptme/gptme/issues/156>`_ - Original feature request
