"""
Server for gptme.
"""

from .app import create_app
from .cli import main

__all__ = ["main", "create_app"]
