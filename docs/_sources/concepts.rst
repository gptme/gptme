Core Concepts
=============

gptme's architecture is built around five core extensibility mechanisms that work together to create a powerful AI assistant platform. Understanding how these concepts relate helps you extend gptme effectively and choose the right approach for your needs.

Architecture Overview
---------------------

.. mermaid::

   graph TD
       K[Knowledge Files] --> A[AI Assistant]
       T[Tools] --> A
       H[Hooks] --> T
       C[Commands] --> A
       P[Plugins] --> T
       P --> H
       P --> C
       A --> U[User]

The five core concepts:

1. **Knowledge Files** - Context and instructions (via :doc:`lessons` and :doc:`skills`)
2. **Tools** - Capabilities the AI can use (see :doc:`tools`)
3. **Hooks** - Lifecycle integration points (see :doc:`hooks`)
4. **Commands** - User interface shortcuts (see :ref:`usage-commands`)
5. **Plugins** - Packaging mechanism for tools/hooks/commands (see :doc:`plugins`)

Knowledge Files (Context Injection)
------------------------------------

**What**: Lightweight knowledge bundles that inject context into conversations.

**Purpose**: Share knowledge, workflows, examples, and best practices with the AI without modifying code.

**Formats**: Two formats are supported:

- **Lessons** (core): Auto-load by keywords/patterns/tools (see :doc:`lessons`)
- **Skills** (Anthropic format): Auto-load by name only (see :doc:`skills`)

**Structure**:

.. code-block:: text

   my-skill/
   ├── SKILL.md          # Main content with YAML frontmatter
   ├── resources/        # Reference materials (optional)
   ├── scripts/          # Utility scripts (optional)
   └── templates/        # Markdown templates (optional)

**Example**:

.. code-block:: markdown

   ---
   name: Python Best Practices
   description: Coding standards for Python projects
   ---

   # Python Best Practices

   When writing Python code:
   - Use type hints
   - Follow PEP 8
   - Write docstrings

   Example:
   ```python
   def greet(name: str) -> str:
       """Greet a person by name."""
       return f"Hello, {name}!"
   ```

**When to use**:

- Sharing knowledge and best practices
- Providing examples and templates
- Guiding workflow and decision-making
- No runtime behavior needed

**Distribution**: ZIP archives, shared via directories

See :doc:`skills` for complete documentation.

Tools (Capabilities)
--------------------

**What**: Functions the AI can execute to interact with the system.

**Purpose**: Extend what the AI can *do* - execute code, read files, browse web, etc.

**Structure**: Python functions with ``ToolSpec`` metadata

**Example**:

.. code-block:: python

   from gptme.tools.base import ToolSpec

   def analyze_code(path: str) -> str:
       """Analyze code quality and suggest improvements."""
       # Implementation
       return "Analysis results..."

   analyze_tool = ToolSpec(
       name="analyze",
       desc="Analyze code quality",
       instructions="Use this to check code quality.",
       functions=[analyze_code],
   )

**When to use**:

- Adding new capabilities (data processing, API calls, etc.)
- Integrating external services
- Providing domain-specific functionality
- Need the AI to *execute* something

**Types of tools**:

- **Built-in**: Included with gptme (see :doc:`tools`)
- **Custom**: User-created (see :doc:`custom_tool`)
- **Plugin**: Distributed as packages (see :doc:`plugins`)

Hooks (Lifecycle Integration)
------------------------------

**What**: Callbacks that execute at specific points in gptme's lifecycle.

**Purpose**: Intercept and modify gptme's behavior at runtime - validate inputs, transform outputs, manage state, etc.

**Hook Types**:

- **Message hooks**: ``PRE_PROCESS``, ``POST_PROCESS``, ``TRANSFORM``
- **Tool hooks**: ``PRE_EXECUTE``, ``POST_EXECUTE``, ``TRANSFORM``
- **File hooks**: ``PRE_SAVE``, ``POST_SAVE``, ``PRE_PATCH``, ``POST_PATCH``
- **Session hooks**: ``START``, ``END``
- **Generation hooks**: ``PRE``, ``POST``, ``INTERRUPT``

**Example**:

.. code-block:: python

   from gptme.hooks import HookType, register_hook

   def lint_before_save(path: str, content: str) -> str:
       """Run linter on code before saving."""
       if path.endswith('.py'):
           # Run linting logic
           return content  # TODO: implement linting logic
       return content

   register_hook(HookType.FILE_PRE_SAVE, lint_before_save)

**When to use**:

- Validating or transforming inputs/outputs
- Adding automatic checks (linting, testing, etc.)
- Managing state or side effects
- Implementing cross-cutting concerns
- Need to *modify* gptme's behavior

**Note**: Hooks are powerful but complex - only use when tools aren't sufficient.

See :doc:`hooks` for complete documentation.

Commands (User Interface)
-------------------------

**What**: Shortcuts for common operations that users type directly.

**Purpose**: Provide convenient interface for frequent actions.

.. _commands:

**Built-in commands**:

- ``/undo`` - Undo last action
- ``/log`` - Show conversation history
- ``/tokens`` - Display token usage
- ``/context`` - Show/modify context files

**Custom commands** (via plugins):

.. code-block:: python

   from gptme.commands import register_command

   def status_command():
       """Show project status."""
       # Implementation
       return "Status: All systems operational"

   register_command("status", status_command)

**When to use**:

- Frequent operations need shortcuts
- User needs direct control
- Complement tool functionality
- Need fast access to information

**Distribution**: Defined in plugins (see :doc:`plugins`)

Plugins (Packaging Mechanism)
------------------------------

**What**: Python packages that bundle tools, hooks, and commands together.

**Purpose**: Distribute complete functionality as installable packages.

**Structure**:

.. code-block:: text

   my_plugin/
   ├── __init__.py          # Plugin metadata
   ├── tools/               # Tool implementations
   │   ├── __init__.py
   │   └── my_tool.py
   ├── hooks/               # Hook implementations
   │   ├── __init__.py
   │   └── my_hook.py
   └── commands/            # Command implementations
       ├── __init__.py
       └── my_command.py

**When to use**:

- Bundling related tools/hooks/commands
- Creating reusable functionality
- Distributing to others
- Need deep integration with gptme runtime

**Examples**:

- `consortium <https://github.com/gptme/gptme-contrib/tree/master/plugins/consortium>`_ - AI collaboration tools
- `imagen <https://github.com/gptme/gptme-contrib/tree/master/plugins/imagen>`_ - Image generation
- `example-hooks <https://github.com/gptme/gptme-contrib/tree/master/plugins/example-hooks>`_ - Hook system demonstrations

See :doc:`plugins` for complete documentation.

How They Work Together
-----------------------

These concepts complement each other to create a flexible extensibility system:

**Example: Code Quality System**

1. **Knowledge File** (skill): Best practices and coding standards

   .. code-block:: markdown

      # Code Quality Standards
      Always run linting before committing code.

2. **Tool**: Execute linter

   .. code-block:: python

      def lint(path: str) -> str:
          """Run linter on code."""
          # Implementation

3. **Hook**: Automatic linting on save

   .. code-block:: python

      register_hook(HookType.FILE_PRE_SAVE, auto_lint)

4. **Command**: Manual lint trigger

   .. code-block:: python

      register_command("lint", lint_command)

5. **Plugin**: Package it all together

   .. code-block:: text

      linting_plugin/
      ├── tools/lint.py     # Linting tool
      ├── hooks/auto.py     # Auto-lint hook
      └── commands/lint.py  # Lint command

**Result**: Complete code quality system that:

- Guides with knowledge (what standards to follow)
- Provides capability (can run linter)
- Integrates automatically (lint on save)
- Offers manual control (lint command)
- Distributes as package (plugin)

Decision Guide
--------------

**When to use each mechanism**:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Mechanism
     - Use When
     - Don't Use When
   * - **Knowledge Files**
     - • Sharing information

       • Providing examples

       • Guiding decisions

       • No runtime behavior needed
     - • Need to execute code

       • Need to modify gptme behavior

       • Require dynamic behavior
   * - **Tools**
     - • Adding capabilities

       • Executing actions

       • Integrating services

       • AI needs to do something
     - • Just sharing knowledge

       • Need to modify gptme's behavior

       • User shortcuts only
   * - **Hooks**
     - • Validating inputs/outputs

       • Automatic checks

       • Cross-cutting concerns

       • Modifying behavior
     - • Can solve with tools

       • Don't need lifecycle integration

       • Just adding capabilities
   * - **Commands**
     - • User shortcuts needed

       • Direct control required

       • Frequent operations

       • Fast access to info
     - • AI should decide when to use

       • Complex operations better as tools

       • Rarely used functionality
   * - **Plugins**
     - • Bundling related functionality

       • Distributing to others

       • Deep integration needed

       • Professional distribution
     - • Single simple tool

       • Personal use only

       • Quick experimentation

Progressive Enhancement
-----------------------

Start simple and add complexity as needed:

1. **Level 1**: Knowledge Files

   - Share knowledge and workflows
   - No code required
   - Portable and simple

2. **Level 2**: Custom Tools

   - Add new capabilities
   - Single Python file
   - Local use

3. **Level 3**: Hooks (if needed)

   - Modify behavior
   - Lifecycle integration
   - More complex

4. **Level 4**: Complete Plugin

   - Bundle everything
   - Professional distribution
   - Full integration

**Example progression**:

.. code-block:: text

   Level 1: deployment-workflow.md (knowledge)
           ↓
   Level 2: deploy.py (custom tool)
           ↓
   Level 3: pre_deploy_check.py (hook)
           ↓
   Level 4: deployment_plugin/ (plugin)

Best Practices
--------------

**Do**:

- ✅ Start with knowledge files (simplest)
- ✅ Use tools for capabilities
- ✅ Add hooks only when necessary
- ✅ Provide commands for common actions
- ✅ Bundle as plugin for distribution
- ✅ Follow single responsibility principle

**Don't**:

- ❌ Mix concerns (knowledge vs. runtime)
- ❌ Use hooks when tools would work
- ❌ Create plugins for single tools
- ❌ Over-engineer solutions
- ❌ Ignore existing mechanisms

Further Reading
---------------

- :doc:`skills` - Knowledge files and skill format
- :doc:`tools` - Built-in tools catalog
- :doc:`custom_tool` - Creating custom tools
- :doc:`hooks` - Hook system details
- :doc:`plugins` - Plugin development guide
- :doc:`examples` - Real-world usage examples

Summary
-------

**The five core concepts work together**:

- **Knowledge Files** provide context and guidance (what to think)
- **Tools** provide capabilities (what AI can do)
- **Hooks** modify behavior (how gptme operates)
- **Commands** offer shortcuts (what users can trigger)
- **Plugins** package functionality (how to distribute)

Choose the right level of complexity for your needs, and progressively enhance as requirements grow.
