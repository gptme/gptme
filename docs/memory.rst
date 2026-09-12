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

Persistent index selection and budget
-------------------------------------

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

Triggered matching
------------------

Add explicit trigger phrases through the shared writer:

.. code-block:: console

   $ gptme-util memory save release-rule "Verify releases before declaring done" --keyword "stable release" --keyword "deploy*" < rule.md
   $ gptme-util memory match "Cut the stable release" --format json

``match`` adapts living entries to gptme's ``LessonMatcher``. Keywords are
case-insensitive substrings; ``*`` matches word characters, never spaces or
punctuation. Empty keywords and a lone ``*`` are disabled. Each matching
keyword contributes one point. Entry names, descriptions and bodies do not
trigger matches. Equal scores retain name order; ``-k`` caps results (default
5). Superseded and historical entries stay excluded. A nearer entry shadows
a same-named farther entry before lifecycle filtering, and index selection
does not restrict matching.

Repeated ``--keyword`` options replace an entry's keywords; omitting the option
preserves existing keywords. The Python writer also accepts ``keywords=[]``
to clear them. Saving keywords follows the existing index policy and budget.
Matching itself is read-only.

``--prompt -`` accepts plain text or a Claude Code JSON event. For
``UserPromptSubmit`` it matches the prompt; for ``PreToolUse`` it matches
string values inside ``tool_input``. Session IDs, transcript paths, and other
envelope fields never participate. ``--pre-tool`` selects the pre-tool response
when supplying plain text. ``--format hook-json`` uses the input event's name
and returns empty context on no match. Text and hook output include source
paths and cap each body at ``--body-chars`` (default 1200).

The CLI keeps no session deduplication state. A harness wrapper can call
``gptme.memory.match.match_memories`` directly and apply its own deduplication
and injection budget to the returned entries, scores and ``matched_by`` reasons.

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
       ],
       "PreToolUse": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "gptme-util memory match --prompt - --format hook-json",
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

Writer provenance
-----------------

Harness wrappers can preserve their session identity through the shared writer::

    gptme-util memory save decision "Reviewed decision" --type reference \
      --metadata '{"originSessionId":"session-123","node_type":"memory"}' < body.md

``--metadata`` accepts a JSON object and merges its keys into existing entry
metadata. Omitted keys survive updates; lifecycle fields and root index policy
remain governed by the store. The reserved ``type`` key must use ``--type``
instead. Invalid JSON, a non-object, or a reserved key fails before writing.
