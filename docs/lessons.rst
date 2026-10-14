:audience: power-user

Lessons
=======

The lesson system provides contextual guidance and best practices that are automatically included in conversations when relevant. Lessons help users follow recommended patterns and avoid common pitfalls.

The lesson system is the **core knowledge system** in gptme. :doc:`skills` are a special case of lessons that follow Anthropic's folder-style format.

Overview
--------

**Lessons** are markdown files with YAML frontmatter that specify when they should be included. The system automatically:

- Indexes lessons from configured directories
- Matches lessons based on **keywords**, **patterns**, and **tools** used
- Includes relevant lessons in conversation context
- Adapts inclusion behavior for interactive vs autonomous modes

Lessons vs Skills
~~~~~~~~~~~~~~~~~

The lesson system supports two formats:

**Lessons (Core Format)**:

- Auto-loading: By keywords, patterns, and tools
- Frontmatter: ``match: {keywords: [...], tools: [...]}``
- Best for: Context-aware guidance that appears automatically
- Example: "Git best practices" appears when discussing commits

**Skills (Anthropic Format)**:

- Auto-loading: By name only (when skill name appears in message)
- Frontmatter: ``name:``, ``description:``
- Best for: Explicit knowledge bundles, portable across tools
- Example: "python-repl" skill loads when you mention "python repl"

+-------------------+------------------------------------+------------------------------------+
| Feature           | Lessons (Core)                     | Skills (Anthropic)                 |
+===================+====================================+====================================+
| Auto-loading      | ✅ Keywords, patterns, tools       | ⚠️ Name only (in message)          |
+-------------------+------------------------------------+------------------------------------+
| Frontmatter       | ``match: {keywords: [...]}``       | ``name:``, ``description:``        |
+-------------------+------------------------------------+------------------------------------+
| Best for          | Context-aware guidance             | Explicit knowledge bundles         |
+-------------------+------------------------------------+------------------------------------+
| Bundled scripts   | No                                 | Yes (optional)                     |
+-------------------+------------------------------------+------------------------------------+

See :doc:`skills` for details on the skills format.

How Lessons Work
----------------

When you start a conversation, gptme:

1. Scans configured lesson directories
2. Indexes lessons with their metadata
3. Monitors the conversation for keywords and tool usage
4. Automatically includes all matching lessons (no per-turn limit)
5. Applies a session-wide limit (default: 20) to prevent context bloat

When exiting, gptme displays a summary of lessons used in the session.

Lessons appear in the conversation context but are hidden by default in the interface. Use ``/log`` to see which lessons are included.

Lesson Format
-------------

Lessons use YAML frontmatter for metadata and Markdown for content:

.. code-block:: markdown

    ---
    match:
      keywords: [keyword1, keyword2, keyword3]
      tools: [tool1, tool2]
    ---

    # Lesson Title

    ## Context
    When this lesson applies...

    ## Pattern
    Recommended approach:
    ```python
    # Example code
    ```

    ## Outcome
    What happens when you follow this pattern...

Metadata Fields
~~~~~~~~~~~~~~~

**match** (required)
  Specifies when the lesson should be included:

  - **keywords**: List of words/phrases that trigger inclusion
  - **tools**: List of tool names that trigger inclusion
  - At least one keyword or tool must be specified

**Example**:

.. code-block:: yaml

    ---
    match:
      keywords: [git, commit, branch]
      tools: [shell]
    ---

Creating Lessons
----------------

Basic Structure
~~~~~~~~~~~~~~~

Create a ``.md`` file in your lessons directory with:

1. YAML frontmatter with match criteria
2. Clear title
3. Context section (when to use)
4. Pattern section (what to do)
5. Outcome section (expected results)

**Example lesson**:

.. code-block:: markdown

    ---
    match:
      keywords: [commit message, git commit]
      tools: [shell]
    ---

    # Git Commit Messages

    ## Context
    When creating git commits in any repository.

    ## Pattern
    Use Conventional Commits format:
    ```
    type(scope): description

    Optional body

    Co-authored-by: Name <email>
    ```

    ## Outcome
    Clear commit history, automated changelog generation.

Lesson Directories
~~~~~~~~~~~~~~~~~~

Lessons are loaded from the following directories (if they exist):

**User-level:**

1. ``~/.config/gptme/lessons/`` - gptme native lessons
2. ``~/.agents/lessons/`` - Cross-platform standard

**Workspace-level:**

3. ``./lessons/`` - Project-specific lessons
4. ``./.gptme/lessons/`` - Hidden project-local lessons

**Other:**

5. ``./.cursor/`` - Cursor rules (auto-translated to keywords)
6. Directories configured in ``gptme.toml``
7. Plugin lessons (auto-discovered from plugin paths)

The ``~/.agents/`` paths provide cross-platform compatibility with other AI tools.

.. note::

   For skill directories (Anthropic SKILL.md format), see :doc:`skills`.

Organize lessons by category:

.. code-block:: text

    lessons/
    ├── tools/           # Tool-specific guidance
    ├── workflows/       # Process and workflow lessons
    ├── patterns/        # General patterns
    └── README.md        # Category overview

Best Practices
~~~~~~~~~~~~~~

**Keywords**:

  - Use specific, relevant terms
  - Include variations (e.g., "commit", "commits", "committing")
  - 3-7 keywords per lesson is typical

**Tools**:

  - Only list tools directly used in the lesson
  - Use exact tool names (e.g., "shell", "python", "browser")

**Content**:

  - Keep lessons concise (< 100 lines preferred)
  - Focus on one specific pattern or issue
  - Include concrete examples
  - Show both anti-patterns and solutions

Configuration
-------------

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

Control lesson behavior with these variables:

.. code-block:: bash

    # Enable/disable auto-include (default: true)
    export GPTME_LESSONS_AUTO_INCLUDE=false

    # Maximum lessons per session (default: 20)
    # This is a session-wide limit - once reached, no more lessons are included
    export GPTME_LESSONS_MAX_SESSION=20

    # Use hybrid keyword + semantic matching (default: false)
    export GPTME_LESSONS_USE_HYBRID=true

    # Hold out specific lessons, e.g. when measuring their effect
    export GPTME_LESSONS_HOLDOUT="lesson-name,another-lesson"

Keyword Extraction
~~~~~~~~~~~~~~~~~~

The system extracts keywords from both user and assistant messages to match relevant lessons. This unified approach ensures lessons are included based on all conversation context, providing guidance during both interactive and autonomous operation.

CLI Commands
------------

Several commands help you work with lessons:

List Lessons
~~~~~~~~~~~~

Show all available lessons:

.. code-block:: bash

    /lesson list

Search Lessons
~~~~~~~~~~~~~~

Find lessons matching a query:

.. code-block:: bash

    /lesson search keyword

Show Lesson Content
~~~~~~~~~~~~~~~~~~~

Display a specific lesson:

.. code-block:: bash

    /lesson show <lesson-id>

Refresh Lessons
~~~~~~~~~~~~~~~

Reload lessons from disk:

.. code-block:: bash

    /lesson refresh

Example Lessons
---------------

The package includes example lessons in ``docs/lessons/``:

**Tools**:

  - ``shell.md`` - Shell command guidelines
  - ``python.md`` - Python development patterns
  - ``browser.md`` - Web browsing best practices
  - ``patch.md`` - File editing patterns

**Workflows**:

  - ``git.md`` - Git workflow and commit conventions

These serve as templates for creating your own lessons.

Migration Guide
---------------

Lessons Without Frontmatter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have existing lessons without YAML frontmatter:

1. They will still work (backward compatible)
2. They won't be auto-included (no match criteria)
3. Add frontmatter to enable auto-inclusion:

.. code-block:: markdown

    ---
    match:
      keywords: [your, keywords, here]
    ---

    # Existing Lesson Title
    ... existing content ...

Converting Lessons
~~~~~~~~~~~~~~~~~~

To convert an existing lesson:

1. Add YAML frontmatter at the top
2. Identify relevant keywords from the content
3. List any tools the lesson references
4. Test matching with ``/lesson search``

**Before**:

.. code-block:: markdown

    # Shell Best Practices

    When using the shell tool...

**After**:

.. code-block:: markdown

    ---
    match:
      keywords: [shell, bash, command]
      tools: [shell]
    ---

    # Shell Best Practices

    When using the shell tool...

Troubleshooting
---------------

Lessons Not Appearing
~~~~~~~~~~~~~~~~~~~~~

If lessons aren't being included:

1. Check indexing: Look for "Indexed n lessons" in logs
2. Verify keywords: Use ``/lesson search`` to test matching
3. Check limits: Ensure ``GPTME_LESSONS_MAX_SESSION`` isn't too low (default: 20)
4. Verify format: Ensure YAML frontmatter is valid
5. Session limit: If resuming a conversation, the session limit may already be reached

Debug Lesson Matching
~~~~~~~~~~~~~~~~~~~~~~

Use verbose logging:

.. code-block:: bash

    gptme --verbose

This shows which lessons match and why.

See Also
--------

- :doc:`skills` - Skills format (Anthropic-style knowledge bundles)
- :doc:`tools` - Available tools that lessons can reference
- :doc:`config` - Configuration options
- :doc:`custom_tool` - Creating custom tools with lessons
- :doc:`agents` - Using lessons with AI agents

Experimental shared advisory selector
-------------------------------------

The production lesson hook can optionally narrow keyword or hybrid matches through
an external JSON selector command. It is disabled by default. This adapter adds no
mandatory dependency on gptme-contrib and never calls a model itself. The shared
``gptme-lessons-extras`` service owns provider selection, cached decisions and
per-session call limits.

Enable it only after installing a compatible service and explicitly identifying
your hard guard lessons. Set these in the process environment (replace the model
with an authorized provider/model):

.. code-block:: shell

   export GPTME_LESSONS_SELECTOR_COMMAND='["python3", "-m", "gptme_lessons_extras.selector", "--context-json", "-", "--state-dir", "/path/to/private/selector-state"]'
   export GPTME_LESSONS_SELECTOR_MODEL='provider/authorized-model'
   export GPTME_LESSONS_SELECTOR_MAX_CALLS=2
   export GPTME_LESSONS_SELECTOR_TIMEOUT=2
   export GPTME_LESSONS_SELECTOR_GUARDS='git-safety,lessons/security/private-data.md'

The command is a JSON argv array, executed without a shell. The executable and its
arguments must be trusted configuration. Selector settings are read only from
the process environment: a repository or conversation ``[env]`` cannot authorize
service execution or provider spending. A nonempty explicit guard list, model
and positive call limit are required; otherwise matching stays deterministic.
Guard identifiers use the same path/name matching as ``GPTME_LESSONS_HOLDOUT``.
Matched ``always_apply`` rules and rules with ``priority: critical`` are also
protected. The advisory selector never suppresses these guards. Existing holdouts,
session limits, deduplication and materialization remain in effect.

The adapter sends at most the eligible advisory catalog, with a selection cap of
three lessons. It keeps the latest user prompt as the selection query; assistant
and tool output do not start another provider request. Intermediate tool events
reuse the shared service's cached decision, or fall back when the cache is absent
or the eligible catalog has changed. Explicit empty selections suppress advisory
matches; malformed, unavailable, over-budget or timed-out results preserve the
complete deterministic baseline. Memory delivery is unchanged.

``GPTME_LESSONS_SELECTOR_MAX_CALLS`` defaults to zero and accepts 0–10 calls per
session. ``GPTME_LESSONS_SELECTOR_TIMEOUT`` defaults to two seconds and must be
finite, positive and at most 30 seconds. The transport allows one additional
second for command startup and serialization. It kills the entire owned process
group on timeout. This experimental adapter currently requires POSIX; other
platforms retain deterministic matching. Debug logs report selection or fallback
reasons. This opt-in transport does not establish selection quality or authorize
a paid rollout.
