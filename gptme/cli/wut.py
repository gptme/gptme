#!/usr/bin/env python3
"""
gptme-wut - Start a gptme session with the current tmux pane content

This script captures the content of the current tmux pane and starts a gptme session
with that content as input, making it easy to get AI assistance with terminal output.
"""

import argparse
import os
import subprocess
import sys


def get_tmux_content(lines: int | None = None):
    """Get content from current tmux pane

    Args:
        lines: Number of lines to capture from the end. If None, captures all lines.
    """
    # Check if we're in tmux by checking environment variables
    if not os.environ.get("TMUX") or not os.environ.get("TMUX_PANE"):
        print("Error: Not in a tmux session", file=sys.stderr)
        sys.exit(1)

    # Capture pane content
    cmd = ["tmux", "capture-pane", "-p"]
    if lines:
        cmd.extend(["-S", f"-{lines}"])

    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=True)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-l", "--lines", type=int, help="Number of lines to capture from the end"
    )
    parser.add_argument(
        "gptme_args", nargs="*", help="Additional arguments to pass to gptme"
    )
    args = parser.parse_args()

    content = get_tmux_content(args.lines)

    # Start gptme with the content and any additional arguments
    cmd = [
        "gptme",
        "<system>The user has some terminal output they need help with and have thus run `gptme-wut` to start this session (ignore it in the output)</system>",
    ] + args.gptme_args
    subprocess.run(cmd, input=content, text=True)


if __name__ == "__main__":
    main()
