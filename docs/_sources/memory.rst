:audience: power-user

Memory
======

gptme can carry context across conversations in several complementary ways:
some you curate yourself, some the agent writes, and some are retrieved
automatically based on what you are working on.

.. list-table::
   :header-rows: 1
   :widths: 22 50 28

   * - System
     - What it is and when it is loaded
     - Read more
   * - Lessons and skills
     - Curated Markdown guidance. Lessons are included automatically when their
       keywords, patterns, or tools match; skills are loaded by name or invoked
       as slash commands (``/skill:<name>``).
     - :doc:`lessons`, :doc:`skills`
   * - Persistent memory (``MEMORY.md``)
     - Memory entries plus ``MEMORY.md`` indexes, in the same format as Claude
       Code's auto-memory. The indexes of every memory root are loaded into the
       workspace prompt at session start.
     - `Cross-harness memory`_
   * - ``memory`` tool
     - Lets the agent save a memory itself during a conversation. Enabled by
       default.
     - `Cross-harness memory`_
   * - ``gptme-util memory``
     - CLI to list, save, audit, and recall memory entries. Usable from any
       harness, including Claude Code and Codex.
     - `Cross-harness memory`_
   * - ``gptme-util knowledge``
     - Problem/resolution pairs stored as JSONL. Matching entries are injected
       once at the start of a conversation when the first prompt has enough
       keywords to search.
     - ``gptme-util knowledge --help``
   * - RAG
     - Indexes and searches documentation, code, and past conversations with
       the ``rag`` tool. Available when ``gptme-rag`` is installed and
       ``[rag]`` is enabled in the project's ``gptme.toml``.
     - :ref:`rag`
   * - Ambient retrieval
     - The ``gptme-retrieval`` plugin from gptme-contrib searches your files
       (qmd, gptme-rag, or grep backends) before each step and injects the
       relevant results, each at most once per conversation.
     - `gptme-retrieval <https://github.com/gptme/gptme-contrib/tree/master/plugins/gptme-retrieval>`_
   * - ``context_cmd``
     - A project command whose output is added to the system prompt. It can
       read the first prompt from ``GPTME_PROMPT_INITIAL`` for query-dependent
       retrieval.
     - :doc:`config`

.. _memory-lessons-skills:

Memory, lessons, or skills?
---------------------------

These systems overlap, but each has a distinct job:

- **Lessons** are curated guidance that shapes how the agent works. They are
  written and reviewed ahead of time, usually versioned in a repository, and
  included automatically when their ``match:`` keywords, patterns, or tools
  come up in a conversation.

- **Skills** are named workflows, loaded when a task calls for them or invoked
  directly as slash commands.

- **Memory** holds facts learned while working: user preferences, decisions and
  their rationale, and project facts that are not obvious from the code. Agents
  and harnesses write it at runtime; the index is always loaded, and full
  entries are found with ``recall``.

- **The knowledge base** (``gptme-util knowledge``) holds problem/resolution
  pairs, so a fix found once can resurface when a similar problem comes up.

Rule of thumb: if it tells the agent *how to behave*, write a lesson; if it is
a *procedure* to run on request, write a skill; if it records *what is true*
about a user or project, save a memory.

Lessons and memory are separate stores, and neither reads the other:

- Lessons are read recursively from lesson directories (``./lessons/``,
  ``./.gptme/lessons/``, ``~/.config/gptme/lessons/``, ``~/.agents/lessons/``,
  and ``[lessons] dirs`` in config) and use ``match:`` frontmatter.

- Memory entries are read from the top level of each memory root
  (``./memory/``, the Claude Code project memory directory,
  ``$GPTME_AGENT_WORKSPACE/memory/``, and ``~/.config/gptme/memory/``, or only
  ``GPTME_MEMORY_DIRS`` when it is set) and use Claude Code-style frontmatter.
  An entry's optional ``keywords`` field only affects ``recall`` ranking.

Keep them in separate directories. There is no tooling that converts between
them; if a memory turns out to be lasting guidance, rewrite it as a lesson and
remove the memory entry.

Cross-harness memory
--------------------

``gptme-util memory`` provides one local, Markdown-based memory store that can
be shared by gptme, Claude Code, Codex, and other agent harnesses. It is
modeled on Claude Code's auto-memory: entries use Claude Code-compatible
frontmatter and are read from layered roots (project, Claude Code, agent, and
user); the nearest entry wins when names collide.

Inspect and write memory
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

   $ gptme-util memory roots
   $ gptme-util memory list
   $ gptme-util memory show prefer-short-answers
   $ gptme-util memory save prefer-short-answers \
       "User prefers short, direct answers." --type feedback < details.md
   $ gptme-util memory index

``index`` prints by default. Pass ``--write`` only when you want to replace the
selected root's ``MEMORY.md`` with a generated index.

Persistent index selection and budget
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A large memory root can keep a small, curated always-on view without removing
living entries from recall. To opt in, create ``.memory-index.json`` inside
that memory directory:

.. code-block:: json

   {
     "version": 1,
     "budget": 21000,
     "selected": ["prefer-short-answers.md", "review-policy.md"]
   }

``selected`` contains unique local filenames, not entry names. Every selected
file must exist and be living. The generated view retains type/name grouping;
the selection list is not a display ordering. Entries omitted from this list
remain available to ``recall``, ``list``, and ``show``. An empty list produces
only the index header.

``index`` (print, ``--write``, and ``--check``), ``save``, and ``supersede`` all
use the persistent UTF-8 byte budget, including headers and newlines. Required
entries are never silently dropped: an oversized view fails before changing
entries, policy, or index. ``--budget`` can tighten the stored cap for one
invocation, but cannot raise it. Edit the policy to change the persistent cap.

In a managed root, ``save`` regenerates the selected view. New entries stay
unselected until explicitly added to the policy. Updating an entry preserves
its lifecycle, provenance, and omitted title/type/metadata; supplied description
and body replace the previous values. Updating an existing entry requires valid
frontmatter, including in a legacy root. Lenient parsing remains available for
reads, but writes refuse malformed YAML instead of silently discarding fields
while attempting a repair. Correct that file's frontmatter before saving again.
``supersede`` transfers a selected old
filename to its replacement, deduplicating the list, and commits both entries,
the policy, and the view together. Ordinary write failures roll back these
replacements; this is not a crash-recovery transaction log.

Before migrating a hand-curated index, copy its link text into entry ``title``
and its instructions into ``description``. Validate a copy with ``index`` and
review both link coverage and wording before ``index --write``. Preserve any
archive as history. Direct file writers and harness hooks must adopt the same
policy; creating the policy does not intercept tools that edit files themselves.
When editing policy by hand, pause concurrent memory writers for that root.

Roots without a policy retain legacy behavior: ``save`` upserts one pointer
line, and explicit index generation includes living entries with an optional
one-shot truncating budget.

Supersession and audit
~~~~~~~~~~~~~~~~~~~~~~

Replace an obsolete entry with an already-existing living entry, then check the
root's strict YAML and bidirectional supersession links:

.. code-block:: console

   $ gptme-util memory supersede old-belief new-belief
   $ gptme-util memory audit
   $ gptme-util memory audit --quiet

``supersede`` updates both entries (``superseded_by`` on the old entry and
``supersedes`` on the replacement) and regenerates the selected root's index.
Both entries must be in the same root; use ``--scope`` when needed. The command
refuses malformed YAML and invalid field types rather than rewriting them
through the lenient read fallback. ``audit`` exits non-zero for malformed
entries, duplicate names, dangling targets, or asymmetric links, making
``audit --quiet`` suitable for lifecycle hooks.

Recall
~~~~~~

Recall searches all living entries across the layered roots:

.. code-block:: console

   $ gptme-util memory recall "How should private code be reviewed?" -k 3
   $ gptme-util memory recall "exact identifier" --format json

With ``gptme-rag`` and its ``lexical`` extra installed, ``--backend auto`` uses
its TF-IDF index. A minimal gptme installation falls back to a stdlib
token-overlap scorer. Output always reports the backend that actually ran; use
``--backend tfidf`` when fallback would be unacceptable.

Claude Code hook
~~~~~~~~~~~~~~~~

Add the command below to a ``UserPromptSubmit`` hook in
``.claude/settings.json``. It reads Claude Code's JSON event from stdin and
returns a valid ``additionalContext`` response:

.. code-block:: json

   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "gptme-util memory recall --prompt - --format hook-json",
               "timeout": 20
             }
           ]
         }
       ]
     }
   }

The hook is read-only and returns empty ``additionalContext`` when there is no
relevant memory. Keep the executable on Claude Code's hook ``PATH``; if the
workspace uses an isolated environment, call its absolute ``gptme-util`` path.

Codex / AGENTS.md integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Codex has no hook mechanism, so memory access is driven by instructions in
``AGENTS.md``. The gptme repository's own ``AGENTS.md`` already contains a
``## Memory`` section; copy or adapt it for any workspace that runs Codex
sessions.

The two key patterns for Codex agents:

**Recall at session start** — surface memories relevant to the current task:

.. code-block:: bash

   gptme-util memory recall "<one-line task description>" -k 5

**Save a memory** — persist something worth keeping across sessions:

.. code-block:: bash

   gptme-util memory save <slug> "<one-line description>" --type <type> <<'EOF'
   <body>
   EOF

Valid ``--type`` values match Claude Code's memory taxonomy: ``user``,
``feedback``, ``project``, ``reference``.

The entry is written to project ``memory/`` when that directory exists,
otherwise to ``~/.claude/projects/<workspace-hash>/memory/``. The selector
checks existence, not writability: an unwritable project directory makes
``save`` fail rather than falling back.

``gptme-util memory recall`` (and the Claude Code hook) search every
layered root, so other harnesses see the entry on their next recall.
gptme's session-start workspace prompt also loads the indexes of every
existing root, within a shared 64 KB budget. Claude Code's native
``MEMORY.md`` auto-load reads only the Claude Code root, so pass
``--scope cc`` when the memory must appear in Claude Code without running
recall.

Writer provenance
~~~~~~~~~~~~~~~~~

Harness wrappers can preserve their session identity through the shared writer::

    gptme-util memory save decision "Reviewed decision" --type reference \
      --metadata '{"originSessionId":"session-123","node_type":"memory"}' < body.md

``--metadata`` accepts a JSON object and merges its keys into existing entry
metadata. Omitted keys survive updates; lifecycle fields and root index policy
remain governed by the store. The reserved ``type`` key must use ``--type``
instead. Invalid JSON, a non-object, or a reserved key fails before writing.
