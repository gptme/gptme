Skills
======

.. note::

   Skills are a **special case of lessons** using Anthropic's folder-style format.
   In gptme, skills auto-load when their **name appears in the message** (e.g.,
   mentioning "python-repl" loads that skill). This differs from lessons which
   auto-load by keywords/patterns/tools. For deep runtime integration, use
   :doc:`plugins`.

The skills system extends gptme's :doc:`lessons` to support bundled tools, scripts,
and workflows inspired by Claude's Skills system and Cursor's rules system.

Overview
--------

**Skills** are lessons that follow Anthropic's format and can bundle:

- Instructional content (like lessons)
- Executable scripts and utilities
- Dependencies and setup requirements

Skills complement lessons by providing **executable components** alongside guidance.

Key Difference: Matching Behavior
---------------------------------

The most important difference between lessons and skills is **how they are auto-loaded**:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Format
     - Auto-loading Trigger
     - Example
   * - **Lessons**
     - Keywords, patterns, tools in conversation
     - Mentioning "git commit" loads git lesson
   * - **Skills**
     - Skill name appears in message
     - Mentioning "python-repl" loads that skill

This means:

- **Lessons** are proactive: they appear when relevant context is detected
- **Skills** are explicit: they appear when specifically mentioned by name

Skill vs. Lesson vs. Plugin
---------------------------

.. list-table::
   :header-rows: 1
   :widths: 15 28 28 29

   * - Feature
     - Lesson
     - Skill
     - Plugin
   * - Purpose
     - Guidance and patterns
     - Executable workflows
     - Deep runtime integration
   * - Auto-loading
     - Keywords, patterns, tools
     - Name only
     - N/A (always loaded)
   * - Content
     - Instructions, examples
     - Instructions + scripts
     - Tools, hooks, commands
   * - Scripts
     - None
     - Bundled helper scripts
     - Via custom tools
   * - Dependencies
     - None
     - Explicit package requirements
     - Python package dependencies
   * - Hooks
     - No
     - No
     - Yes
   * - Custom Tools
     - No
     - No
     - Yes
   * - Frontmatter
     - ``match: {keywords, tools}``
     - ``name:``, ``description:``
     - N/A

**When to use**:

- **Lesson**: Teaching patterns, best practices, tool usage
- **Skill**: Providing reusable scripts, automated workflows (lightweight)
- **Plugin**: Runtime hooks, custom tools, deep gptme integration (see :doc:`plugins`)

Skill Format
------------

Skills use YAML frontmatter following Anthropic's format:

.. code-block:: yaml

    ---
    name: skill-name
    description: Brief description of what the skill does and when to use it
    ---

    # Skill Title

    Skill description and usage instructions...

.. note::

   Dependencies are specified in ``requirements.txt``, and bundled scripts are
   placed in the same directory as ``SKILL.md``.

Directory Structure
-------------------

Skills are organized parallel to lessons:

.. code-block:: text

    gptme/
    └── lessons/           # Unified knowledge tree
        ├── tools/        # Tool-specific lessons
        ├── patterns/     # General patterns
        ├── workflows/    # Workflow lessons
        └── skills/       # Skills (Anthropic format)
            └── python-repl/
                ├── SKILL.md
                ├── python_helpers.py
                └── requirements.txt

Skill Loading Directories
-------------------------

Skills are loaded from the following directories (if they exist):

**User-level:**

1. ``~/.config/gptme/skills/`` - gptme native skills
2. ``~/.claude/skills/`` - Claude CLI compatibility (share skills with Claude CLI)
3. ``~/.agents/skills/`` - Cross-platform standard

**Workspace-level:**

4. ``./skills/`` - Project-specific skills
5. ``./.gptme/skills/`` - Hidden project-local skills

The ``~/.agents/`` and ``~/.claude/`` paths provide cross-platform compatibility,
enabling skills to be shared between gptme and other AI tools.

Creating Skills
---------------

1. Design the Skill
~~~~~~~~~~~~~~~~~~~

Identify:

- What workflow or automation does it provide?
- What scripts/utilities are needed?
- What dependencies are required?

2. Create Skill Directory
~~~~~~~~~~~~~~~~~~~~~~~~~

Create a directory under ``gptme/lessons/skills/skill-name/`` with these files:

**SKILL.md** (Anthropic format):

.. code-block:: yaml

    ---
    name: skill-name
    description: Brief description of what the skill does
    ---

    # Skill Title

    ## Overview
    Detailed description and use cases.

    ## Bundled Scripts
    Describe each included script.

    ## Usage Patterns
    Show common usage examples.

    ## Dependencies
    List required packages (detailed in requirements.txt).

**requirements.txt**:

.. code-block:: text

    # List of required packages
    numpy
    pandas

3. Create Bundled Scripts
~~~~~~~~~~~~~~~~~~~~~~~~~

Create helper scripts in the same directory as the skill:

.. code-block:: python

    #!/usr/bin/env python3
    """Helper script for skill."""

    def helper_function():
        """Does something useful."""
        pass

4. Test the Skill
~~~~~~~~~~~~~~~~~

.. code-block:: python

    from gptme.lessons.parser import parse_lesson
    from pathlib import Path

    # Parse skill from unified lessons tree
    skill = parse_lesson(Path("gptme/lessons/skills/my-skill/SKILL.md"))
    assert skill.metadata.name == "my-skill"
    assert skill.metadata.description

Deep Integration with Plugins
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**For runtime integration (hooks, custom tools, commands), use the** :doc:`plugins`.

Skills are lightweight knowledge bundles that remain simple. For deeper integration
with gptme's runtime:

- **Hooks**: Register lifecycle callbacks (see :doc:`hooks`)
- **Custom Tools**: Add new capabilities (see :ref:`creating-a-plugin`)
- **Commands**: Add CLI commands (see :ref:`plugin-command-modules`)

**Example**: For a skill that needs hooks, create a plugin instead:

.. code-block:: python

    # In a plugin: my_plugin/hooks/setup.py
    from gptme.hooks import HookType, register_hook

    def setup_environment(logdir, workspace, initial_msgs):
        """Initialize environment at session start."""
        # Your hook logic here
        yield

    def register():
        register_hook("my_plugin.setup", HookType.SESSION_START, setup_environment)

See :doc:`plugins` for complete examples.

Use Cases
---------

Data Analysis Skill
~~~~~~~~~~~~~~~~~~~

- Bundles pandas, numpy helpers
- Auto-imports common libraries
- Provides data inspection utilities
- Includes plotting helpers

Testing Skill
~~~~~~~~~~~~~

- Bundles pytest configuration
- Provides test utilities
- Auto-discovers tests
- Formats test reports

API Development Skill
~~~~~~~~~~~~~~~~~~~~~

- Bundles FastAPI templates
- Provides auth helpers
- Includes validation utilities
- Auto-generates OpenAPI docs

Integration with Lessons
------------------------

Skills complement lessons:

- **Lesson teaches** the pattern
- **Skill provides** the tooling

**Common pattern**: A lesson can suggest relevant skills. Since lessons auto-load by
keywords while skills require explicit mention, a lesson can bridge this gap:

.. code-block:: markdown

    ---
    match:
      keywords: [data analysis, pandas, dataframe]
    ---

    # Data Analysis Best Practices

    When analyzing data, follow these patterns...

    ## Related Skills

    For bundled utilities, mention "python-repl" to load helper functions.

This allows keyword-triggered guidance to point users toward relevant skills.

Example:

- Lesson: ``lessons/patterns/testing.md`` - Testing best practices
- Skill: ``skills/testing-skill.md`` - Bundled pytest utilities

Related
-------

- :doc:`lessons` - Core knowledge system
- :doc:`plugins` - For hooks, custom tools, and deep integration
- :doc:`hooks` - Lifecycle callbacks (plugins only)
- `Issue #686 <https://github.com/gptme/gptme/issues/686>`_ - Phase 4: Skills Integration
- `Issue #1170 <https://github.com/gptme/gptme/issues/1170>`_ - Phase 4.2+ roadmap
- `Claude Skills <https://simonwillison.net/2025/Oct/10/claude-skills/>`_ - Inspiration
