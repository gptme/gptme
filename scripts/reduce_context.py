#!/usr/bin/env python3
"""
reduce_context.py - Reduce context bloat in gptme conversation files

This script processes gptme conversation files (.toml) to reduce context bloat by:
1. Removing assistant thinking sections (<think>...</think>)
2. Simplifying workspace context sections in system messages
3. Removing file contents from error messages and failed patches
4. Simplifying code blocks containing file contents
5. Removing stdout output blocks

Intended Use Case:
This script is designed to process TOML files retrieved by running the `/edit`
command in a gptme conversation. It helps reduce the context size of large
conversations so they can be continued without hitting token limits.

The idea is allow for a simple way to test strategies for reducing context size,
without having to modify gptme directly.

Usage:
    python reduce_context.py conversation.toml [options]

Options:
    -o, --output FILE   Specify output file (default: input_file.reduced.toml)
    --dry-run           Print stats only, don't write output
    -v, --verbose       Print detailed info about replacements
    -t, --top N         Show the N longest messages after processing (default: 5)
    --validate          Validate the output TOML file after processing

Examples:
    # Basic usage
    python reduce_context.py convo.toml

    # Specify output file
    python reduce_context.py convo.toml -o convo-reduced.toml

    # Show top 10 largest messages
    python reduce_context.py convo.toml -t 10

    # Validate the output
    python reduce_context.py convo.toml --validate
"""

import argparse
import re
from pathlib import Path

import tomli as tomllib
import tomli_w


def remove_thinking_sections(content):
    """Remove <think>...</think> sections from content."""
    # Fix incomplete thinking sections (no closing tag)
    if "<think>" in content and "</think>" not in content:
        content = re.sub(
            r"<think>.*?$", "[incomplete thinking removed]", content, flags=re.DOTALL
        )

    # Remove normal thinking sections
    content = re.sub(
        r"<think>.*?</think>", "[thinking removed]", content, flags=re.DOTALL
    )

    return content


def simplify_workspace_context(content):
    """Simplify workspace context in system messages."""
    if "# Workspace Context" in content:
        return re.sub(
            r"# Workspace Context.*?$",
            "[workspace context removed]",
            content,
            flags=re.DOTALL,
        )
    return content


def simplify_file_contents_in_errors(content):
    """Simplify file content displays in error messages."""
    if "Here are the actual file contents:" in content:
        return re.sub(
            r"(Error during execution:.*?\n)Here are the actual file contents:.*?$",
            r"\1[file contents removed]",
            content,
            flags=re.DOTALL,
        )
    return content


def simplify_failed_patches(content):
    """Simplify failed patch blocks in messages."""
    if "Patch failed:" in content:
        return re.sub(
            r"(Patch failed:.*?\n)```.*?```",
            r"\1[failed patch content removed]",
            content,
            flags=re.DOTALL,
        )
    return content


def simplify_precommit_failures(content):
    """Simplify pre-commit failure messages."""
    if "Pre-commit checks failed" in content:
        return re.sub(
            r"(Pre-commit checks failed.*?\n)```stdout\n.*?```",
            r"\1[pre-commit output removed]",
            content,
            flags=re.DOTALL,
        )
    return content


def remove_stdout_blocks(content):
    """Remove all stdout code blocks."""
    return re.sub(
        r"```stdout\n.*?```",
        "```stdout\n[stdout output removed]\n```",
        content,
        flags=re.DOTALL,
    )


def process_code_blocks(content):
    """Process code blocks with file contents."""
    code_block_pattern = re.compile(r"```(.*?)```", re.DOTALL)

    def process_code_block(match):
        code_block = match.group(0)
        code_block_header = code_block.split("\n")[0]

        # Directory listings
        if code_block_header == "```" and re.search(r"```\n\.\n", code_block):
            return "```\n[directory listing removed]\n```"

        # File contents with paths
        if match := re.search(r"```(/[^`\n]*?)\n", code_block):
            file_path = match.group(1)
            return f"```{file_path}\n[file contents removed]\n```"

        # Specific file types
        if (
            re.search(r"```(.*?README\.md)", code_block_header)
            or code_block_header == "```README.md"
        ):
            return "```README.md\n[README.md contents removed]\n```"

        if (
            re.search(r"```(.*?Makefile)", code_block_header)
            or code_block_header == "```Makefile"
        ):
            return "```Makefile\n[Makefile contents removed]\n```"

        # Otherwise keep the code block unchanged
        return code_block

    return code_block_pattern.sub(process_code_block, content)


def process_message_content(role, content):
    """Process a message's content based on its role."""
    if role == "system":
        # Process system messages
        content = simplify_workspace_context(content)
        content = simplify_file_contents_in_errors(content)
        content = simplify_failed_patches(content)
        content = simplify_precommit_failures(content)
        content = remove_stdout_blocks(content)
    elif role == "assistant":
        # Process assistant messages
        content = remove_thinking_sections(content)
        content = process_code_blocks(content)
        content = remove_stdout_blocks(content)

    # No processing for user messages or other roles
    return content


def process_conversation(
    input_file, output_file=None, dry_run=False, verbose=False, top_messages=5
):
    """
    Process a gptme conversation file to reduce context bloat.

    Args:
        input_file: Path to the input conversation file
        output_file: Path to write the processed file (defaults to input_file.reduced.toml)
        dry_run: If True, print stats only without writing output
        verbose: If True, print detailed info about removals
        top_messages: Number of top longest messages to display
    """
    # Read the input file as binary (required for tomllib)
    with open(input_file, "rb") as f:
        data = tomllib.load(f)
        messages = data.get("messages", [])

    # Record original metrics
    original_size = sum(len(str(m)) for m in messages)

    # Process each message
    processed_messages = []
    message_sizes = []

    for message in messages:
        # Extract role and content
        role = message.get("role", "unknown")
        content = message.get("content", "")

        # Process the content
        processed_content = process_message_content(role, content)

        # Create a new message with processed content
        processed_message = message.copy()
        processed_message["content"] = processed_content
        processed_messages.append(processed_message)

        # Track message size for reporting
        size = len(str(processed_message))
        preview = processed_content.replace("\n", " ")[:100]
        if len(preview) == 100:
            preview += "..."

        message_sizes.append((size, role, preview))

    # Calculate metrics
    processed_size = sum(len(str(m)) for m in processed_messages)
    processed_content = str(processed_messages)
    reduction_pct = (original_size - processed_size) / original_size * 100

    # Print stats
    print(f"Original size: {original_size} bytes")
    print(f"Processed size: {processed_size} bytes")
    print(f"Reduction: {reduction_pct:.2f}%")

    # Print longest remaining messages
    if top_messages > 0:
        print(f"\nTop {top_messages} longest messages after processing:")
        message_sizes.sort(reverse=True)  # Sort by size, largest first
        for i, (size, role, preview) in enumerate(message_sizes[:top_messages]):
            print(f"{i + 1}. {size} bytes: {role}: {preview}")

    # Write output unless dry run
    if not dry_run:
        if output_file is None:
            output_file = Path(input_file).with_suffix(".reduced.toml")
        else:
            output_file = Path(output_file)

        # Create a proper TOML document
        output_data = {"messages": processed_messages}

        # Write using tomli_w
        with open(output_file, "wb") as f:
            tomli_w.dump(output_data, f)
        print(f"Processed file written to: {output_file}")
        return output_file
    print("Dry run - no file written")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Reduce context bloat in gptme conversation files"
    )
    parser.add_argument("input_file", help="Input conversation file to process")
    parser.add_argument(
        "-o", "--output", help="Output file path (default: input_file.reduced.toml)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print stats only, don't write output"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed info about replacements",
    )
    parser.add_argument(
        "-t",
        "--top",
        type=int,
        default=5,
        help="Show the N longest messages after processing (default: 5, 0 to disable)",
    )
    parser.add_argument(
        "--validate", action="store_true", help="Validate the output TOML file"
    )
    args = parser.parse_args()

    output_file = process_conversation(
        args.input_file, args.output, args.dry_run, args.verbose, args.top
    )

    # Validate the output if requested and not in dry-run mode
    if args.validate and not args.dry_run and output_file:
        try:
            with open(output_file, "rb") as f:
                tomllib.load(f)
            print("TOML validation: Output file is valid TOML.")
        except Exception as e:
            print(f"WARNING: Output file is not valid TOML: {e}")


if __name__ == "__main__":
    main()
