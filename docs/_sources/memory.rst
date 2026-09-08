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
