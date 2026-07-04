"""Pre-action confirmation gate for sensitive computer-use actions.

Sensitive actions are those that handle potentially private data:
  computer():      type, key, left_click_drag
  fill_element():  browser form fills

Gating is opt-in via GPTME_COMPUTER_CONFIRM_SENSITIVE:
  (unset or "0") — gate disabled, actions proceed silently (default, back-compat)
  "1"            — gate enabled; interactive sessions prompt, non-interactive block
  "auto-allow"   — gate enabled but auto-approves (useful in tests)

When gating is enabled and the session is non-interactive (no TTY on stdin),
sensitive actions raise PermissionError unless GPTME_COMPUTER_CONFIRM_SENSITIVE
is set to "auto-allow".
"""

from __future__ import annotations

import os
import sys

#: Desktop actions that handle potentially private data.
GATE_ACTIONS_COMPUTER: frozenset[str] = frozenset({"key", "type", "left_click_drag"})

#: Browser actions that handle potentially private data.
GATE_ACTIONS_BROWSER: frozenset[str] = frozenset({"fill_element"})

VALID_GATE_MODES: frozenset[str] = frozenset({"", "0", "1", "auto-allow"})


def _gate_mode() -> str:
    """Return the current gate mode from the environment.

    Values:
      ""           — gate disabled
      "0"          — gate disabled (explicit)
      "1"          — gate enabled (prompt in TTY, block otherwise)
      "auto-allow" — gate enabled but auto-approves without prompting
    """
    raw_mode = os.environ.get("GPTME_COMPUTER_CONFIRM_SENSITIVE", "")
    mode = raw_mode.strip().lower()
    if mode not in VALID_GATE_MODES:
        raise ValueError(
            "Invalid GPTME_COMPUTER_CONFIRM_SENSITIVE value "
            f"{raw_mode!r}; expected one of: unset, '0', '1', 'auto-allow'."
        )
    return mode


def sensitive_action_gate(
    action: str,
    text: str | None = None,
    *,
    is_browser: bool = False,
) -> None:
    """Block or prompt before a sensitive computer-use action executes.

    Call this *before* executing any action in GATE_ACTIONS_COMPUTER or
    GATE_ACTIONS_BROWSER.  Does nothing if the gate is disabled (default).

    Args:
        action: The action name (e.g. "type", "fill_element").
        text: The text/value to be entered (used to compute display length only;
              content is never shown).
        is_browser: True when called from the browser tool (fill_element).

    Raises:
        PermissionError: If the gate is enabled and the action is denied,
            either by the user (interactive) or automatically (non-interactive).
    """
    gate_set = GATE_ACTIONS_BROWSER if is_browser else GATE_ACTIONS_COMPUTER
    if action not in gate_set:
        return

    mode = _gate_mode()
    if not mode or mode == "0":
        return  # gate disabled — default; back-compatible

    if mode == "auto-allow":
        return  # gate enabled but unconditionally approves

    # Build a short, non-revealing description
    if text:
        detail = f"({len(text)} chars, content hidden)"
    else:
        detail = ""

    if not sys.stdin.isatty():
        raise PermissionError(
            f"computer: sensitive action '{action}' blocked in non-interactive mode "
            f"{detail}. "
            "Set GPTME_COMPUTER_CONFIRM_SENSITIVE=auto-allow to permit in scripts."
        )

    # Interactive: ask the user
    print(
        f"\n[computer] Sensitive action: {action}  {detail}",
        file=sys.stderr,
    )
    try:
        answer = input("Allow? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""

    if answer not in ("y", "yes"):
        raise PermissionError(f"computer: sensitive action '{action}' denied by user.")
