"""
Sets up a KeyboardInterrupt handler to handle Ctrl-C during the chat loop.
"""

import os
import time
from contextvars import ContextVar

from . import console

# Use ContextVar for thread/session isolation
# In server/multi-session scenarios, each session has its own interrupt state
_interruptible_var: ContextVar[bool] = ContextVar("interruptible", default=False)
_last_interrupt_time_var: ContextVar[float] = ContextVar(
    "last_interrupt_time", default=0.0
)


def handle_keyboard_interrupt(signum, frame):  # pragma: no cover
    """
    This handler allows interruption of the assistant or tool execution when in an interruptible state,
    while still providing a safeguard against accidental exits during user input.
    """
    current_time = time.time()

    # if testing with pytest
    testing = bool(os.getenv("PYTEST_CURRENT_TEST"))

    if _interruptible_var.get() or testing:
        raise KeyboardInterrupt

    # if current_time - last_interrupt_time <= timeout:
    #     console.log("Second interrupt received, exiting...")
    #     sys.exit(0)

    _last_interrupt_time_var.set(current_time)
    console.print()
    # console.log(
    #     f"Interrupt received. Press Ctrl-C again within {timeout} seconds to exit."
    # )
    console.log("Interrupted. Press Ctrl-D to exit.")


def set_interruptible():
    """Set the interruptible flag for the current context/session."""
    _interruptible_var.set(True)


def clear_interruptible():
    """Clear the interruptible flag for the current context/session."""
    _interruptible_var.set(False)
