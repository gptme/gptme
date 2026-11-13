:audience: power-user

Sessions
========

Commands for handing off, resuming, checkpointing, and analyzing gptme sessions.

Local peer conversations
------------------------

Agents in separate gptme conversations can exchange attributed messages without
sharing a parent/subagent relationship::

   gptme chats peers
   gptme chats send TARGET --peer --sender SOURCE "Review my current approach"
   gptme chats send TARGET --peer --sender SOURCE --branch experiment "New evidence"

Inside an agent session, the ``chats`` tool exposes ``list_peers()`` and
``send_peer_message(conversation_id, message)``. The sender defaults to the
current conversation. Discovery is advisory and reports whether a local runtime
has an addressable gptme conversation; it does not grant access to another user
or support injecting into unrelated external harnesses.

Messages use the existing durable prompt queue. Updated TUI and server receivers
can wake while idle; busy receivers consume peer input at a safe step boundary.
A TUI wake preserves the unsent draft. Paused/interrupted or autowake-disabled
receivers leave input queued. Plain CLI idle wake is not provided by this path.
Both sender and receiver must use a version with peer support for idle wake.

Peer messages carry a sender conversation, PID and correlation ID. These are
local same-user attribution, not authenticated identity or human approval. A
successful send means *queued*, not read or executed. Do not automatically reply
to every peer message or create unbounded agent-to-agent ping-pong loops.

Command reference
-----------------

.. click:: gptme.cli.cmd_status:status
   :prog: gptme-status
   :nested: full

.. click:: gptme.cli.cmd_resume:resume
   :prog: gptme-resume
   :nested: full

.. click:: gptme.cli.checkpoint:main
   :prog: gptme-checkpoint
   :nested: full

.. click:: gptme.cli.cmd_stats:stats
   :prog: gptme-stats
   :nested: full
