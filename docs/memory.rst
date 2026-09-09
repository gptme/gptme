Cross-harness memory
====================

``gptme-util memory`` provides one local, Markdown-based memory store that can
be shared by gptme, Claude Code, Codex, and other agent harnesses. Entries use
Claude Code-compatible frontmatter and are read from layered roots (project,
Claude Code, agent, and user); the nearest entry wins when names collide.

Inspect and write memory
------------------------

.. code-block:: console

   $ gptme-util memory roots
   $ gptme-util memory list
   $ gptme-util memory show prefer-short-answers
   $ gptme-util memory save prefer-short-answers \
       "User prefers short, direct answers." --type feedback < details.md
   $ gptme-util memory index

``index`` prints by default. Pass ``--write`` only when you want to replace the
selected root's ``MEMORY.md`` with a generated index.

Supersession and audit
----------------------

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
------

Recall searches all living entries across the layered roots:

.. code-block:: console

   $ gptme-util memory recall "How should private code be reviewed?" -k 3
   $ gptme-util memory recall "exact identifier" --format json

With ``gptme-rag`` and its ``lexical`` extra installed, ``--backend auto`` uses
its TF-IDF index. A minimal gptme installation falls back to a stdlib
token-overlap scorer. Output always reports the backend that actually ran; use
``--backend tfidf`` when fallback would be unacceptable.

Claude Code hook
----------------

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
------------------------------

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
gptme's session-start workspace prompt and Claude Code's native
``MEMORY.md`` auto-load only the Claude Code root. Pass ``--scope cc``
when the memory must appear on that auto-load path without running
recall.
