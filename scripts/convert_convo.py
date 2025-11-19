#!/usr/bin/env python3
"""Convert gptme conversation logs from JSONL to individual markdown files.

This script takes a conversation.jsonl file and converts each message into a separate
markdown file with YAML frontmatter. The output files are organized by message index
and role, with configurable filename formats.

Features:
- Preserves message metadata (role, timestamp, hide, pinned status)
- Configurable output directory and filename format
- Generates statistics about message counts by role
- YAML frontmatter for metadata
- Verbose mode for progress tracking

Use cases:
- Easy search across conversations with tools like grep/ripgrep
- Quick preview of messages in any markdown editor or viewer
- Flexible organization by date or role using different filename formats
- Version control friendly (one message per file, plain text)
- Easy to process with standard Unix tools (cat, sort, awk, etc.)

Example usage:
    # Basic conversion with default format (by index)
    ./convert_convo.py conversation.jsonl output/

    # Sort by timestamp for chronological browsing
    ./convert_convo.py conversation.jsonl -v --format "{timestamp}-{role}.md"

    # Group by role for easy filtering
    ./convert_convo.py conversation.jsonl -v --format "{role}/{index:04d}.md"
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from dateutil.parser import isoparse


class Stats(TypedDict):
    total: int
    by_role: dict[str, int]


def convert_conversation(
    jsonl_path: str,
    output_dir: str | None = None,
    verbose: bool = False,
    filename_format: str = "{index:04d}-{role}.md",
) -> None:
    """Convert a conversation.jsonl file to individual markdown files.

    Args:
        jsonl_path: Path to the conversation.jsonl file
        output_dir: Directory to write markdown files to (default: markdown/ next to input)
        verbose: Whether to print progress messages
        filename_format: Format string for filenames. Available variables:
                        {index}: Message number
                        {role}: Message role
                        {timestamp}: Message timestamp
    """
    input_path = Path(jsonl_path)
    if not input_path.exists():
        print(f"Error: Input file not found: {jsonl_path}")
        sys.exit(1)

    # If no output dir specified, create one next to the input file
    output_path = Path(output_dir) if output_dir else input_path.parent / "markdown"

    # Create output directory if it doesn't exist
    output_path.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"Converting {jsonl_path} to markdown files in {output_path}")

    # Track statistics
    stats: Stats = {"total": 0, "by_role": {}}

    # Read and convert each message
    with open(input_path) as f:
        for i, line in enumerate(f):
            # Parse JSON
            msg = json.loads(line)

            # Extract fields
            role = msg["role"]
            content = msg["content"]
            timestamp = msg.get("timestamp", "")
            hide = msg.get("hide", False)
            pinned = msg.get("pinned", False)

            # Format timestamp for filename if needed
            dt = isoparse(timestamp) if timestamp else datetime.now()
            timestamp_str = dt.strftime("%Y%m%d-%H%M%S")

            # Create filename using format string
            filename = filename_format.format(
                index=i, role=role, timestamp=timestamp_str
            )
            filepath = output_path / filename

            # Update statistics
            stats["total"] += 1
            stats["by_role"][role] = stats["by_role"].get(role, 0) + 1

            # Write markdown file with YAML frontmatter
            with open(filepath, "w") as outf:
                outf.write("---\n")
                outf.write(f"role: {role}\n")
                outf.write(f"timestamp: {timestamp}\n")
                if hide:
                    outf.write("hide: true\n")
                if pinned:
                    outf.write("pinned: true\n")
                outf.write("---\n\n")
                outf.write(content)
                outf.write("\n")

            if verbose:
                print(f"Created {filename}")

    # Print summary
    print("\nConversion complete!")
    print(f"Total messages: {stats['total']}")
    print("\nMessages by role:")
    for role, count in stats["by_role"].items():
        print(f"  {role}: {count}")


def main() -> None:
    if len(sys.argv) < 2:
        print("""Usage: convert_convo.py <conversation.jsonl> [options]

Options:
    [output_dir]                 Directory to write markdown files to
    -v, --verbose               Show progress messages
    -f, --format FORMAT         Custom filename format. Available variables:
                               {index}: Message number (e.g. 0001)
                               {role}: Message role (e.g. user)
                               {timestamp}: Message timestamp (e.g. 20250506-192832)
                               Default: {index:04d}-{role}.md""")
        sys.exit(1)

    # Parse arguments
    jsonl_path = sys.argv[1]
    args = sys.argv[2:]

    output_dir: str | None = None
    verbose = False
    filename_format = "{index:04d}-{role}.md"

    while args:
        arg = args.pop(0)
        if arg in ["-v", "--verbose"]:
            verbose = True
        elif arg in ["-f", "--format"]:
            if not args:
                print("Error: --format requires a format string")
                sys.exit(1)
            filename_format = args.pop(0)
        else:
            output_dir = arg

    convert_conversation(jsonl_path, output_dir, verbose, filename_format)


if __name__ == "__main__":
    main()
