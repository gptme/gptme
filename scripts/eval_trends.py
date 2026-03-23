#!/usr/bin/env python3
"""Thin shim — functionality has moved to gptme.eval.trends."""

from gptme.eval.trends import *  # noqa: F403
from gptme.eval.trends import main

if __name__ == "__main__":
    main()
