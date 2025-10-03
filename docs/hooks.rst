Hooks
=====

.. note::
   This is a new feature added in response to `issue #156 <https://github.com/gptme/gptme/issues/156>`_.

The hook system allows tools and plugins to register callbacks that execute at various points in gptme's lifecycle. This enables powerful extensions like automatic linting, memory management, pre-commit checks, and more.

Hook Types
----------

The following hook types are available:

Message Lifecycle Hooks
~~~~~~~~~~~~~~~~~~~~~~~~

- ``MESSAGE_PRE_PROCESS``: Before processing a user message
- ``MESSAGE_POST_PROCESS``: After message processing completes
- ``MESSAGE_TRANSFORM``: Transform message content before processing

Tool Lifecycle Hooks
~~~~~~~~~~~~~~~~~~~~~

- ``TOOL_PRE_EXECUTE``: Before executing any tool
- ``TOOL_POST_EXECUTE``: After executing any tool
- ``TOOL_TRANSFORM``: Transform tool execution

File Operation Hooks
~~~~~~~~~~~~~~~~~~~~~

- ``FILE_PRE_SAVE``: Before saving a file
- ``FILE_POST_SAVE``: After saving a file
- ``FILE_PRE_PATCH``: Before patching a file
- ``FILE_POST_PATCH``: After patching a file

Session Lifecycle Hooks
~~~~~~~~~~~~~~~~~~~~~~~~

- ``SESSION_START``: At session start
- ``SESSION_END``: At session end

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
               HookType.FILE_POST_SAVE.value,  # Hook type
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

   def my_hook_function(log, workspace):
       """Custom hook function."""
       # Do something
       return Message("system", "Hook executed!")

   register_hook(
       name="my_custom_hook",
       hook_type=HookType.MESSAGE_PRE_PROCESS,
       func=my_hook_function,
       priority=0,
       enabled=True
   )

Hook Function Signatures
~~~~~~~~~~~~~~~~~~~~~~~~~

Hook functions receive different arguments depending on the hook type:

.. code-block:: python

   # Message hooks
   def message_hook(log, workspace):
       pass

   # Tool hooks
   def tool_hook(tool_name, tool_use):
       pass

   # File hooks
   def file_hook(path, content, created=False):
       pass

   # Session hooks
   def session_hook(logdir, workspace, manager=None, initial_msgs=None):
       pass

Hook functions can:

- Return ``None`` (no action)
- Return a single ``Message`` object
- Return a generator that yields ``Message`` objects
- Raise exceptions (which are caught and logged)

Managing Hooks
--------------

Query Hooks
~~~~~~~~~~~

.. code-block:: python

   from gptme.hooks import get_hooks, HookType

   # Get all hooks
   all_hooks = get_hooks()

   # Get hooks of a specific type
   tool_hooks = get_hooks(HookType.TOOL_POST_EXECUTE)

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
   unregister_hook("my_hook", HookType.FILE_POST_SAVE)

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
               HookType.FILE_POST_SAVE.value,
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
           "lint": (HookType.FILE_POST_SAVE.value, lint_file, 5)
       }
   )

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

Hooks can be configured via environment variables:

.. code-block:: bash

   # Example: disable specific hooks
   export GPTME_HOOKS_DISABLED="linter.lint,precommit.precommit_check"

   # Example: set hook priorities
   export GPTME_HOOK_PRIORITY_LINTER=20

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
           "check": (HookType.MESSAGE_POST_PROCESS.value, precommit_hook, 5)
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
