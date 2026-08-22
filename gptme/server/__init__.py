"""
Server for gptme.
"""

import signal as _signal
import sys as _sys


# Install a minimal SIGTERM handler immediately — before the slow Flask/app
# imports that follow — so SIGTERM during startup produces diagnostic output
# rather than silently terminating the process (gptme/gptme#3589).
# The cli module replaces this with a logger-aware version after init_logging().
def _startup_sigterm_handler(signum: int, frame) -> None:
    _sys.stderr.write("Received SIGTERM during startup, shutting down gracefully\n")
    _sys.stderr.flush()
    raise KeyboardInterrupt


_signal.signal(_signal.SIGTERM, _startup_sigterm_handler)

from .app import create_app
from .cli import main

__all__ = ["main", "create_app"]
