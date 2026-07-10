"""Utilities for JSON-RPC stdio transports."""

from __future__ import annotations

import os
import sys
from typing import IO


def capture_stdio_transport() -> tuple[IO[bytes], IO[bytes]]:
    """Capture real stdin/stdout fds, then redirect fd 1 to fd 2.

    After this call:
    - The returned (stdin_file, stdout_file) are the only way to talk to the
      real stdin/stdout JSON-RPC channel.
    - fd 1 points to stderr, so print(), sys.stdout.write(), and C extensions
      writing to stdout cannot corrupt the protocol stream.
    - No monkey-patching or import-order sensitivity is required.
    """
    real_stdin_fd = os.dup(0)
    real_stdout_fd = os.dup(1)

    os.dup2(2, 1)
    sys.stdout = open(1, "w", buffering=1, closefd=False)

    real_stdin = os.fdopen(real_stdin_fd, "rb", buffering=0)
    real_stdout = os.fdopen(real_stdout_fd, "wb", buffering=0)

    return real_stdin, real_stdout
