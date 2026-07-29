Cookbook
========

A collection of patterns for common gptme workflows. Each pattern shows a
concrete problem, the idiomatic solution, and a runnable example.

.. contents::
   :local:
   :depth: 2

Tool Use Basics
---------------

**Difficulty**: Beginner | **Category**: Tool Use

**Problem**

New gptme users often treat it as a chatbot. The real power is that gptme can
*act* — read and write files, run commands, and inspect output — all in one
conversational session without leaving the terminal.

**Solution**

gptme ships with built-in tools that are always available:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Tool
     - What it does
   * - ``shell``
     - Run any shell command and capture stdout/stderr
   * - ``save`` / ``patch``
     - Write or edit files
   * - ``ipython``
     - Execute Python code in a persistent interpreter
   * - ``read``
     - Read file contents into context
   * - ``browser``
     - Fetch a URL (if enabled)

The agent orchestrates these tools automatically based on your request. You
don't need to invoke them explicitly.

**Example**

Start a session and ask a compound question that requires multiple tools:

.. code-block:: bash

    gptme "Read README.md, list the project's dependencies from pyproject.toml, and tell me if any are pinned to an old major version"

gptme will read both files, reason about version ranges, and reply with its
findings.

For interactive iteration, start a session and keep chatting:

.. code-block:: bash

    gptme  # opens an interactive session
    # > Read the failing tests and fix them
    # > Now run the test suite and show me the output
    # > Write a summary of what you changed to CHANGES.md

**Notes**

- Tools run in your local environment with your permissions. Keep sessions
  scoped to the project directory.

- Use ``gptme tools list`` to see which tools are available and which are
  enabled by default.

- Add ``--no-confirm`` to skip per-tool confirmation prompts in trusted sessions.

----

Parallel Research Pipeline
--------------------------

**Difficulty**: Intermediate | **Category**: Multi-Agent

**Problem**

Deep research takes time when done sequentially. If you need to compare five
frameworks or investigate three bug hypotheses, doing them one-by-one wastes
wall-clock time and burns context on already-answered sub-questions.

**Solution**

gptme supports spawning subagents via the ``subagent`` tool. Each subagent gets
a clean context and runs its task independently. The coordinator waits for all
subagents to finish, then synthesizes the results.

This pattern works well for:

- Comparing N alternatives simultaneously
- Running independent research branches
- Executing the same task against multiple inputs

**Example**

Create a coordinator prompt file and run it:

.. code-block:: bash

    cat > research-prompt.md << 'EOF'
    Research the following three Python HTTP client libraries in parallel:
    1. httpx
    2. aiohttp
    3. requests

    For each, find: latest version, async support, connection pooling behavior, and
    known issues with timeouts. Then write a comparison table to comparison.md and
    recommend one for a high-concurrency microservice.
    EOF

    gptme --tools +subagent "$(cat research-prompt.md)"

Or invoke multi-agent mode directly in an interactive session:

.. code-block:: bash

    gptme
    # > Spawn three subagents: one researches httpx, one aiohttp, one requests.
    # > Each should write its findings to a temp file named after the library.
    # > When all are done, synthesize the findings into comparison.md.

The coordinator agent will spawn three subagents with separate contexts, each
researching one library. Results are written to shared files; the coordinator
reads them and writes the synthesis.

**Notes**

- Subagents share your filesystem but not context. Use files as the
  communication channel between coordinator and workers.

- For independent tasks, the total wall-clock time is roughly
  ``max(subtask times)`` rather than ``sum(subtask times)``.

- Keep each subagent's prompt self-contained — it won't see the coordinator's
  conversation history.

----

Persistent Context with Skills
-------------------------------

**Difficulty**: Intermediate | **Category**: Context Management

**Problem**

Every new gptme session starts blank. If your project has conventions (a
specific naming scheme, a non-obvious architecture decision, a required workflow
before running tests), you end up pasting the same explanation repeatedly or
getting wrong first drafts because the agent lacked context.

**Solution**

A *skill* is a plain markdown document (``SKILL.md``) that gptme injects into
the session context automatically when its name is requested. Skills are placed
in named subdirectories under ``skills/`` (or ``~/.config/gptme/skills/`` for
user-global skills).

This means you write the explanation **once**, commit it, and gptme makes it
available on demand — no copy-pasting, no per-session setup.

**Example**

Create a skill for your project's database migration convention:

.. code-block:: bash

    mkdir -p skills/db-migration
    cat > skills/db-migration/SKILL.md << 'EOF'
    ---
    name: db-migration
    description: Django migration conventions and pre-migration checklist
    ---

    # Migration Workflow

    ## Before creating a migration

    1. Run `make lint` — schema changes that fail lint cause corrupt migrations.
    2. Squash pending migrations if there are more than 10.
    3. Never create a migration on an unmerged feature branch.

    ## Creating a migration

    ```bash
    python manage.py makemigrations --name describe_the_change
    ```

    ## Reverting a migration

    ```bash
    python manage.py migrate myapp 0042  # the migration before yours
    ```
    EOF

gptme automatically discovers skills in the ``skills/`` directory at your
project root (no configuration needed). Alternatively, place skills in
``~/.config/gptme/skills/`` for user-global availability across all projects.

Skills are loaded by name — request one explicitly in a session:

.. code-block:: bash

    gptme "Using the db-migration skill, help me add a nullable email field to the User model"

**Notes**

- Skills are plain markdown files — commit them to your project repo so all
  contributors benefit from them.

- Skills work across runtimes (gptme terminal, Claude Code with the hook
  adapter). Write them once, use them everywhere.

- See :doc:`skills` for the full skills reference.

----

Compose Skills into Workflows
------------------------------

**Difficulty**: Advanced | **Category**: Skill Composition

**Problem**

Real-world workflows are multi-step: run tests, update a changelog, bump a
version, create a tag, post a release. Writing a single monolithic prompt for
this is brittle — it's hard to update one step without breaking others, and you
can't reuse the individual steps elsewhere.

**Solution**

gptme skills compose explicitly. A workflow skill is a sequenced list of steps,
while focused sub-skills hold the detailed conventions for each step. Name the
workflow and every sub-skill you want loaded in the prompt; mentioning the
workflow alone does not recursively load skills that it references.

This gives you a library of reusable primitives that snap together into larger
workflows without duplicating their detailed instructions.

**Example**

Three focused sub-skills, each in its own ``SKILL.md``:

.. code-block:: bash

    # skills/release-check-tests/SKILL.md
    ---
    name: release-check-tests
    description: Verify the test suite passes before any release action
    ---
    # Check Tests Before Release

    Always run the full test suite before any release action:

        make test

    If any test fails, STOP and fix it before proceeding.

.. code-block:: bash

    # skills/release-update-changelog/SKILL.md
    ---
    name: release-update-changelog
    description: Changelog update convention for releases
    ---
    # Changelog Update Convention

    Format: `## [X.Y.Z] - YYYY-MM-DD` with sections Added / Changed / Fixed / Removed.
    Always add the new version block at the TOP of CHANGELOG.md, above previous versions.
    Never delete old entries.

.. code-block:: bash

    # skills/release-tag-and-push/SKILL.md
    ---
    name: release-tag-and-push
    description: Git tagging and push convention for releases
    ---
    # Tag and Push Convention

    Create an annotated tag:

        git tag -a v$VERSION -m "Release v$VERSION" && git push origin master --tags

    Do not push without a passing CI run on master.

A workflow skill that chains all three into a single step:

.. code-block:: bash

    # skills/release-workflow/SKILL.md
    ---
    name: release-workflow
    description: Full release pipeline — test, changelog, tag, push
    ---
    # Release Workflow

    Steps in order:
    1. Run `make test` (stop on failure)
    2. Update CHANGELOG.md with the new version
    3. Commit the changelog: `git commit CHANGELOG.md -m "chore: update changelog for vX.Y.Z"`
    4. Create the annotated tag and push: `git tag -a v$VERSION -m "Release v$VERSION" && git push origin master --tags`

Trigger the full workflow and load its three focused conventions with one
prompt:

.. code-block:: bash

    gptme "Using the release-workflow, release-check-tests, release-update-changelog, and release-tag-and-push skills, do a release for v1.4.2"

**Notes**

- Keep sub-skills under ~50 lines each. Long skills dilute focus; split them.

- Skills are versioned in git — use ``git log skills/`` to audit how your
  workflows have evolved over time.

- See :doc:`skills` for the full skill format reference.

----

Write a Custom Tool Plugin
--------------------------

**Difficulty**: Advanced | **Category**: Custom Tools

**Problem**

gptme's built-in tools cover general-purpose tasks, but domain-specific
operations — querying a proprietary API, reading a custom binary format, calling
an internal service — require a custom tool. Without one, you're pasting raw
output into the prompt by hand, which is slow and error-prone.

**Solution**

gptme has a plugin API. A plugin is a Python package that exposes a
``GptmePlugin`` instance via the ``gptme.plugins`` entry-point group. Each tool
in the plugin is a ``ToolSpec`` with a name, description (shown to the LLM), and
a ``functions`` list of Python callables the LLM can invoke directly. Write typed
functions with docstrings and gptme surfaces them as structured tool calls.

**Example**

A plugin that lets gptme query a local SQLite database:

.. code-block:: python

    # my_gptme_plugin/__init__.py
    import sqlite3
    from gptme.tools.base import ToolSpec
    from gptme.plugins.plugin import GptmePlugin


    def query_db(db_path: str, sql: str) -> str:
        """Execute a read-only SQL query against a local SQLite database.

        Args:
            db_path: Path to the SQLite database file.
            sql: SELECT statement to run.

        Returns:
            Query results as a Markdown table, or "(no results)" when no rows match.
        """
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        if not cols:
            return "(no results)"
        header = " | ".join(cols)
        sep = " | ".join("---" for _ in cols)
        body = "\n".join(" | ".join(str(c) for c in row) for row in rows)
        return f"{header}\n{sep}\n{body}"


    plugin = GptmePlugin(
        name="sqlite",
        tools=[
            ToolSpec(
                name="query_db",
                desc="Run a read-only SQL query against a local SQLite database and return results as a Markdown table.",
                functions=[query_db],
            )
        ],
    )

.. code-block:: toml

    # pyproject.toml (in the plugin package)
    [project]
    name = "my-gptme-plugin"
    version = "0.1.0"

    [project.entry-points."gptme.plugins"]
    sqlite = "my_gptme_plugin:plugin"

Install and use:

.. code-block:: bash

    pip install -e ./my_gptme_plugin

    # Verify it loaded
    gptme tools list

    # Use it in a session
    gptme "Query the users table in /var/app/prod.db and find accounts created in the last 7 days"

gptme will call ``query_db`` with the right arguments and show you the results
inline, without you needing to copy-paste any SQL output.

**Notes**

- Use ``mode=ro`` in the SQLite URI (as shown) to prevent accidental writes from
  a buggy SQL statement the LLM generates.

- Keep tool descriptions concise but precise — the LLM reads the ``desc`` field
  to decide when and how to call the tool.

- gptme derives the tool's parameter schema from the function's type annotations
  and docstring ``Args:`` section; keep both accurate.

- See :doc:`plugins` for the full ``GptmePlugin`` and ``ToolSpec`` API reference.
