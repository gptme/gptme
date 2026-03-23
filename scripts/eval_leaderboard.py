#!/usr/bin/env python3
"""Thin shim — functionality has moved to gptme.eval.leaderboard."""

from gptme.eval.leaderboard import *  # noqa: F403
from gptme.eval.leaderboard import main

if __name__ == "__main__":
    main()
