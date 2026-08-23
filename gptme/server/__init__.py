"""
Server for gptme.
"""

__all__ = ["create_app", "main"]


def __getattr__(name: str):
    # Lazy imports so that ``import gptme.server`` does not eagerly load Flask
    # and other heavyweight dependencies. The SIGTERM startup handler lives in
    # cli.py and must be installed before the slow init phase (model loading,
    # telemetry). Eager imports here would defer the handler installation,
    # causing SIGTERM during init to fail silently (gptme/gptme#3589).
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "main":
        from .cli import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
