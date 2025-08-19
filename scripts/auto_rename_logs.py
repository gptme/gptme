#!/usr/bin/env python3

"""Script to auto-rename gptme conversation logs."""

import argparse
import logging

from gptme.init import init, init_logging
from gptme.util.auto_naming import generate_llm_name as generate_name
from gptme.logmanager import LogManager
from rich import print as rprint

logger = logging.getLogger(__name__)


def initialize_gptme(verbose: bool):
    """Initialize gptme with required configuration."""
    init_logging(verbose)
    init(
        model=None,  # Let init handle model selection from config
        interactive=False,
        tool_allowlist=None,
    )


from gptme.dirs import get_logs_dir
from gptme.util.generate_name import is_generated_name


def auto_rename_logs(dry_run: bool = True, limit: int = 10) -> None:
    """Auto-rename gptme conversation logs.

    Args:
        dry_run: If True, only show what would be renamed
        limit: Maximum number of conversations to rename (default: 10)
    """
    rprint("Scanning for conversations to rename...")

    # Get logs directory
    logs_dir = get_logs_dir()
    if not logs_dir.exists():
        print(f"Error: Logs directory not found: {logs_dir}")
        return

    # Find and sort all conversation logs
    renamed = 0
    conv_dirs = sorted(logs_dir.iterdir())
    for conv_dir in conv_dirs:
        if renamed >= limit:  # Stop when we've renamed enough
            break

        if not conv_dir.is_dir():
            continue

        try:
            # Extract the actual name (after date if present)
            parts = conv_dir.name.split("-")
            if (
                len(parts) >= 4 and parts[0].isdigit()
            ):  # Has date prefix (YYYY-MM-DD-name)
                conv_name = "-".join(parts[3:])
            else:
                conv_name = conv_dir.name

            if not is_generated_name(conv_name):
                rprint(f"[orange3]⏭️  Skipping {conv_dir.name}: not a generated name[/]")
                continue

            # Load conversation
            try:
                manager = LogManager.load(conv_dir, lock=False)
            except FileNotFoundError:
                rprint(f"[orange3]⏭️  Skipping {conv_dir.name}: no conversation file[/]")
                continue

            # Skip if no messages
            if not manager.log.messages:
                rprint(f"[orange3]⏭️  Skipping {conv_dir.name}: empty conversation[/]")
                continue

            # Generate new name from user and assistant messages (skip system)
            conversation_msgs = [
                msg for msg in manager.log.messages if msg.role in ("user", "assistant")
            ]
            new_name = generate_name(conversation_msgs)

            # Skip if name is invalid
            if " " in new_name:
                rprint(
                    f"[orange3]⏭️  Skipping {conv_dir.name}: invalid generated name[/]"
                )
                continue

            # Get date prefix if it exists
            date_prefix = (
                parts[0] + "-" + parts[1] + "-" + parts[2] + "-"
                if len(parts) >= 4 and parts[0].isdigit()
                else ""
            )

            # Skip if name is same as current
            if new_name == conv_name:
                rprint(f"[orange3]⏭️  Skipping {conv_dir.name}: name unchanged[/]")
                continue

            # Find available name (add suffix if needed)
            base_new_name = new_name
            suffix = 0
            while True:
                full_new_name = (
                    f"{date_prefix}{base_new_name}{'-' + str(suffix) if suffix else ''}"
                )
                target_path = logs_dir / full_new_name
                if not target_path.exists() or suffix > 100:  # prevent infinite loop
                    break
                suffix += 1

            if suffix > 100:
                rprint(
                    f"[orange3]⏭️  Skipping {conv_dir.name}: too many name conflicts[/]"
                )
                continue

            # Rename
            if dry_run:
                rprint(f"[cyan]🔄 Would rename: {conv_dir.name} -> {full_new_name}[/]")
            else:
                rprint(f"[green]✅ Renaming: {conv_dir.name} -> {full_new_name}[/]")
                # TODO: fix this to use the new ChatConfig.name option
                raise NotImplementedError(
                    "Not reimplemented after adding chat config `name` option for display-names"
                )
                # manager.rename(
                #     base_new_name + ("-" + str(suffix) if suffix else ""),
                #     keep_date=True,
                # )

            renamed += 1

        except Exception as e:
            print(f"Error processing {conv_dir}: {e}")

    if renamed == 0:
        rprint("[orange3]No conversations found that need renaming.[/]")
    else:
        if dry_run:
            rprint(f"\n[cyan]Would rename {renamed} conversations.[/]")
        else:
            rprint(f"\n[green]Renamed {renamed} conversations.[/]")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-dry-run", action="store_true", help="Actually perform the renames"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of conversations to rename",
    )
    args = parser.parse_args()

    initialize_gptme(verbose=False)
    auto_rename_logs(dry_run=not args.no_dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
