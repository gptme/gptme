"""Textual-based TUI for gptme.

A richer, interactive terminal UI complementary to the plain CLI
(which remains better suited for non-interactive/scripted use).

Key features over the CLI:

- **Prompt queueing** (#569): type and submit new prompts while the agent
  is working; they are queued and dispatched when the current turn finishes.
- **Compact, expandable output**: tool output is rendered in collapsible
  sections (like HTML ``<details>``), keeping the conversation scannable.
- **Persistent input + status bar** (#1821): input stays at the bottom while
  output streams above; the status bar shows model, token usage and state.

Requires the ``tui`` extra: ``pipx install 'gptme[tui]'``.
"""
