"""
Server for gptme.
"""

from .app import create_app

__all__ = ["create_app"]


def __getattr__(name: str):
    # Lazy import of `main` so that ``import gptme.server`` does not eagerly
    # load the CLI module and its heavyweight gptme/Flask dependencies.  The
    # SIGTERM startup handler lives in cli.py and is only needed when the
    # server CLI is actually invoked — not when another process uses the
    # server's Python API (gptme/gptme#3589).
    if name == "main":
        from .cli import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
