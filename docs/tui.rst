TUI
===

gptme ships an optional Textual-based terminal UI, ``gptme-tui``, complementary
to the plain :doc:`CLI <cli>` (which remains better suited for non-interactive
and scripted use).

It addresses two long-standing UX limitations of the plain CLI
(see :issue:`569`):

- **Prompt queueing**: type and submit new prompts while the agent is working.
  They are shown dimmed in the conversation and dispatched automatically when
  the current turn finishes.

- **Compact, expandable output**: tool output is collapsed to a one-line
  summary by default (like HTML ``<details>``); click it or press
  :kbd:`Ctrl+O` to expand.

It also provides a persistent status bar showing the current model, token
usage relative to the context window, and agent state.

Installation
------------

The TUI requires the ``tui`` extra::

    pipx install 'gptme[tui]'

Usage
-----

Start a new conversation in the current directory::

    gptme-tui

Resume the most recent conversation::

    gptme-tui --resume

Conversations are stored in the same format and location as CLI conversations,
so they can be opened interchangeably: start in the TUI, resume in the CLI
(``gptme --resume``), or vice versa (``gptme-tui -n <name>``).

Keys
----

===============  ============================================================
Key              Action
===============  ============================================================
:kbd:`Enter`     Send prompt (queues it if the agent is busy)
:kbd:`Alt+Enter` Insert newline (multi-line prompts; :kbd:`Ctrl+J` also works)
:kbd:`Tab`       Complete slash-commands and their arguments
:kbd:`Escape`    Interrupt generation
:kbd:`Ctrl+C`    Interrupt generation, or quit when idle
:kbd:`Ctrl+D`    Quit
:kbd:`Ctrl+O`    Expand/collapse all tool outputs
===============  ============================================================

When a tool is about to execute, a confirmation dialog shows a preview;
press :kbd:`y` to execute, :kbd:`n` to skip, or :kbd:`a` to auto-confirm for
the rest of the session.

Commands
--------

The TUI supports the same :doc:`slash-commands <commands>` as the CLI
(``/model``, ``/undo``, ``/tokens``, …), with the same Tab completion,
by routing them through the shared command registry. Command output is
shown inline in the conversation. ``/quit`` is a TUI-local alias for
``/exit``.

Limitations
-----------

The TUI is young and intentionally minimal. Commands that need an external
terminal program (e.g. ``/edit`` spawning ``$EDITOR``) don't work yet; resume
the conversation in the CLI for those. Non-interactive/scripted use should
keep using ``gptme`` directly.
