:audience: power-user

Recovering chat message content
===============================

``gptme-util chats read ID`` keeps short, one-line previews by default. Use
``--full`` for multiline message content; each message is capped at 10,000
characters by default (``--max-chars`` accepts 1 through 100,000). Attachments
are not expanded into their original bytes.

Text search results and ``read --start`` use **1-based original-log message
numbers**, including system messages even when they are hidden. JSON search
retains the zero-based ``snippets[].index`` for compatibility and adds
``snippets[].message_number`` for direct use with ``--start``. Older text search
used zero-based numbers, and older reads numbered only visible messages; do not
reuse those old displayed coordinates without converting them.

``--context`` counts visible messages before the selected position and is included
in ``--limit``. Changing ``--system`` changes visibility, not message numbers.
When full content is truncated, the continuation marker reports the same message
number and next character offset. Continue with ``--start N --full --limit 1
--content-offset OFFSET``. Offsets count Unicode characters, not encoded bytes;
nonzero offsets require a single-message read with no context. As with any
position-based read, these coordinates assume the history has not been edited.

.. click:: gptme.cli.util:main
   :prog: gptme-util
   :nested: full
