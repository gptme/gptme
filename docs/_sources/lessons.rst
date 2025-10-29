Lessons
=======

The lesson system provides contextual guidance and best practices that are automatically included in conversations when relevant. Lessons help users follow recommended patterns and avoid common pitfalls.

Overview
--------

Lessons are markdown files with YAML frontmatter that specify when they should be included. The system automatically:

- Indexes lessons from configured directories
- Matches lessons based on keywords and tools used
- Includes relevant lessons in conversation context
- Adapts inclusion behavior for interactive vs autonomous modes

How Lessons Work
----------------

When you start a conversation, gptme:

1. Scans configured lesson directories
2. Indexes lessons with their metadata
3. Monitors the conversation for keywords and tool usage
4. Automatically includes matching lessons
5. Limits the number of included lessons to preserve context

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

Lessons are loaded from:

1. ``~/.config/gptme/lessons/`` - User-specific lessons
2. ``./lessons/`` - Project-specific lessons (if present)
3. Package lessons (examples in gptme)

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

    # Maximum lessons to include (default: 5)
    export GPTME_LESSONS_MAX_INCLUDED=3

    # Refresh lessons each message (default: false)
    export GPTME_LESSONS_REFRESH=true

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
3. Check limits: Ensure ``GPTME_LESSONS_MAX_INCLUDED`` isn't too low
4. Verify format: Ensure YAML frontmatter is valid

Debug Lesson Matching
~~~~~~~~~~~~~~~~~~~~~~

Use verbose logging:

.. code-block:: bash

    gptme --verbose

This shows which lessons match and why.

See Also
--------

- :doc:`tools` - Available tools that lessons can reference
- :doc:`config` - Configuration options
- :doc:`custom_tool` - Creating custom tools with lessons
- :doc:`agents` - Using lessons with AI agents
