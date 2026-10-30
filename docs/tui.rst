:audience: user

TUI
===

gptme ships an optional Textual-based terminal UI, ``gptme-tui``, complementary
to the plain :doc:`CLI <cli>` (which remains better suited for non-interactive
and scripted use).

.. image:: ../media/screenshots/tui.png
   :alt: gptme-tui showing a conversation, collapsed tool output, and the status bar
   :width: 100%

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

Asynchronous work
-----------------

Child-agent results and fired watches appear in their originating conversation
and branch, including after the agent has finished its turn. By default they
also resume an idle agent without another prompt. Progress updates are displayed
without starting generation. Unsent text and its cursor position are preserved;
results arriving during generation, a command or a confirmation wait for a safe
boundary. Results for another branch wait until you return to that branch.

Set ``watch_autowake = false`` under ``[chat]`` in the conversation's
``config.toml`` for notification-only delivery. Tool and URL confirmations still
apply to any resumed generation. An interrupt suppresses automatic continuation.
This does not change plain CLI wakeups or non-interactive shutdown policy.

Installation
------------

The TUI requires the ``tui`` extra::

    pipx install 'gptme[tui]'

Usage
-----

Start a new conversation in the current directory::

    gptme-tui

Or start with a prompt, like with ``gptme`` (chain several with ``-``)::

    gptme-tui "write a script that counts lines" - "now add tests"

Pick a conversation to resume from a list (or start a new one), or resume
a specific one by name::

    gptme-tui --resume
    gptme-tui --resume <name>

Conversations are stored in the same format and location as CLI conversations,
so they can be opened interchangeably: start in the TUI, resume in the CLI
(``gptme --resume``), or vice versa (``gptme-tui -n <name>``).

To switch without leaving the conversation, use ``/restart cli`` in the TUI or
``/restart tui`` in the CLI. ``/restart web`` opens the conversation in the
:doc:`web UI <webui>` of a running ``gptme-server``
(see :doc:`commands`).

Inline mode (experimental)
--------------------------

By default the TUI runs in the alternate screen with its own scrollable chat
view. With ``--inline`` it instead renders like Claude Code: messages are
printed into the terminal's **native scrollback** while only a small live
region (streaming preview, input, status bar) stays at the bottom::

    gptme-tui --inline

Terminal/tmux scrolling then works normally, and the transcript stays in your
scrollback after exit. Prompts submitted while the agent works are listed
above the input until they are sent. Trade-offs: past tool output can't be
expanded in place (:kbd:`Ctrl+O` instead toggles whether *future* tool output
and thinking print expanded), mouse interaction is left entirely to the terminal, and
dragging the terminal narrower can leave fragments of the input area in the
scrollback.

Display settings
----------------

``/display`` changes what the TUI shows; it never changes what the model does
(for reasoning effort, see :ref:`reasoning-effort`). Run it alone to list the
current settings.

- ``/display thinking [on|off]``: model thinking. Hidden by default: a
  finished message shows a one-line placeholder, and the live preview shows
  only the response. Set ``GPTME_TUI_DISPLAY_THINKING=1`` to show it from
  startup.
- ``/display outputs [on|off]``: expand or collapse tool output.
- ``/display hidden [on|off]``: messages sent to the model but normally not
  shown, such as token-usage and time notices. Off by default; set
  ``GPTME_TUI_DISPLAY_HIDDEN=1`` to show them from startup.
- ``/display highlight [on|off]``: syntax-highlight commands in tool-call
  titles and output summaries. On by default; set
  ``GPTME_TUI_DISPLAY_HIGHLIGHT=0`` to turn it off from startup.
- ``/display grouping [on|off]``: group an agent turn's steps into one block: the agent's
  name once, each tool's output indented under the call that ran it. Output from hooks
  and other notices keeps its own block, and nothing is shown out of order.
  Off shows one block per message, as stored in the log. On by default; set
  ``GPTME_TUI_DISPLAY_GROUPING=0`` to turn it off from startup. Full-screen mode
  only.

:kbd:`Ctrl+O` is the shorthand for both: it expands tool output and thinking,
or collapses both when both are already expanded.

Without ``on``/``off`` the setting toggles. In the default view the change
applies to existing messages; in inline mode it applies to messages printed
afterwards.

Status line
-----------

The status bar shows the model, context use, cost and what the agent is
doing. To render your own instead, for example with the same script as your
Claude Code status line, set a command:

.. code-block:: toml

    # ~/.config/gptme/config.toml
    [tui]
    statusline = "python3 ~/.claude/scripts/statusline.py"

See :ref:`tui-statusline` for what the command receives.

Keys
----

=================  ==========================================================
Key                Action
=================  ==========================================================
:kbd:`Enter`       Send prompt (queues it if the agent is busy)
:kbd:`Alt+Enter`   Insert newline (:kbd:`Ctrl+J` also works)
:kbd:`Tab`         Complete slash-commands and their arguments
:kbd:`Escape`      Interrupt generation
:kbd:`Ctrl+C`      Interrupt generation, or quit when idle
:kbd:`Ctrl+D`      Quit
:kbd:`Ctrl+O`      Expand/collapse all tool outputs and thinking
=================  ==========================================================

A prompt sent while the agent is working is queued. It joins the running
turn before the agent's next step, so you can steer it without waiting for
the turn to finish. If it contains URLs, you're asked whether to fetch them
when you queue it. The queue stays in order: a slash-command waits until the
turn ends, and so does everything queued after it.

When a tool is about to execute, a confirmation dialog shows a preview;
press :kbd:`y` to execute, :kbd:`n` to skip, or :kbd:`a` to auto-confirm for
the rest of the session.

Commands
--------

The TUI supports the same :doc:`slash-commands <commands>` as the CLI
(``/model``, ``/undo``, ``/tokens``, …), with the same Tab completion,
by routing them through the shared command registry. Command output is
shown inline in the conversation. ``/quit`` is a TUI-local alias for
``/exit``, and ``/display`` (see `Display settings`_) is TUI-only.

Outside commands, Tab completes the last word as a file path, adding a
trailing ``/`` to directories. Prefix a path with ``@`` to attach it:
``@journal/``, ``@./notes.md``, or ``@README.md``. Completion preserves that
prefix and wraps names with spaces in backticks, such as ```@my notes.txt```.
Bare paths remain text, including when completed with Tab. See
:ref:`prompt-attachments` for the shared CLI/TUI policy and legacy opt-in.

Limitations
-----------

The TUI is young and intentionally minimal. Commands that need an external
terminal program (e.g. ``/edit`` spawning ``$EDITOR``) don't work yet; switch
the conversation to the CLI for those with ``/restart cli``. Non-interactive/scripted use should
keep using ``gptme`` directly.
