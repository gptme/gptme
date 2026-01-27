"""
CLI module for gptme.

This module contains all CLI-related code, separated from core logic.
"""

from ..chat import chat
from .main import main

__all__ = ["main", "chat"]
