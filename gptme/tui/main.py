"""Entry point for the gptme TUI (``gptme-tui``)."""

import logging
import os
import sys
from pathlib import Path

import click

from ..config import setup_config_from_cli
from ..dirs import get_logs_dir
from ..init import init, init_logging
from ..logmanager import LogManager
from ..message import set_output_format
from ..prompts import get_prompt
from ..tools import get_tools
from ..util.auto_naming import generate_conversation_id

logger = logging.getLogger(__name__)


def _get_logdir(name: str) -> Path:
    logs_dir = get_logs_dir()
    if name == "random":
        name = generate_conversation_id(name="random", logs_dir=logs_dir)
    logdir = logs_dir / name
    logdir.mkdir(parents=True, exist_ok=True)
    return logdir


def _get_logdir_resume() -> Path:
    """Get the most recently modified conversation."""
    logs_dir = get_logs_dir()
    candidates = sorted(
        (d for d in logs_dir.iterdir() if (d / "conversation.jsonl").exists()),
        key=lambda d: (d / "conversation.jsonl").stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise click.UsageError("No conversation found to resume.")
    return candidates[0]


def _print_history(manager: LogManager, limit: int = 50) -> None:
    """Print past messages to the terminal before an inline session starts."""
    from rich.console import Console

    from .app import renderables_for_message

    console = Console()
    msgs = [m for m in manager.log if not m.hide]
    if len(msgs) > limit:
        console.print(f"[dim]… {len(msgs) - limit} earlier messages not shown[/dim]")
        msgs = msgs[-limit:]
    for msg in msgs:
        for renderable in renderables_for_message(msg):
            console.print(renderable)


@click.command("gptme-tui")
@click.option(
    "-n", "--name", default="random", help="Conversation name to open or create."
)
@click.option("-r", "--resume", is_flag=True, help="Resume the last conversation.")
@click.option(
    "-m",
    "--model",
    default=None,
    help="Model to use (e.g. anthropic/claude-sonnet-4-6).",
)
@click.option(
    "-w",
    "--workspace",
    default=None,
    help="Workspace directory (default: current directory).",
)
@click.option(
    "-t",
    "--tools",
    "tool_allowlist",
    default=None,
    help="Comma-separated list of tools to allow.",
)
@click.option(
    "--tool-format",
    "tool_format",
    default=None,
    type=click.Choice(["markdown", "xml", "tool"]),
)
@click.option("--no-confirm", is_flag=True, help="Skip tool confirmation prompts.")
@click.option(
    "--inline",
    is_flag=True,
    help="Experimental: render inline without the alternate screen, printing "
    "messages into the terminal's native scrollback (tmux/terminal scrolling "
    "works normally, like Claude Code).",
)
@click.option(
    "--experimental-jelly-errors",
    is_flag=True,
    help="Animate TUI error messages and show recovery hints.",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging.")
def main(
    name: str,
    resume: bool,
    model: str | None,
    workspace: str | None,
    tool_allowlist: str | None,
    tool_format: str | None,
    no_confirm: bool,
    inline: bool,
    experimental_jelly_errors: bool,
    verbose: bool,
) -> None:
    """gptme TUI — interactive terminal UI for gptme.

    Complementary to the plain `gptme` CLI: supports queueing prompts while
    the agent works, collapsible tool output, and a live status bar.
    """
    try:
        from .app import GptmeApp
    except ImportError as e:
        raise click.ClickException(
            "The TUI requires the 'textual' package. "
            "Install with: pipx install 'gptme[tui]'"
        ) from e

    init_logging(verbose)
    # quiet: suppress terminal printing from core machinery; the TUI renders
    # messages itself (streaming via on_token, results via step() yields)
    set_output_format("quiet")

    logdir = _get_logdir_resume() if resume else _get_logdir(name)
    workspace_path = Path(workspace).expanduser().resolve() if workspace else Path.cwd()

    config = setup_config_from_cli(
        workspace=workspace_path,
        logdir=logdir,
        model=model,
        tool_allowlist=tool_allowlist,
        tool_format=tool_format,  # type: ignore[arg-type]
        interactive=True,
    )
    assert config.chat and config.chat.tool_format

    # The TUI is always interactive: never load the `complete` tool, which is
    # meant for autonomous sessions (matches CLI behavior, where it is only
    # added in --non-interactive mode). Saved conversation configs from
    # autonomous runs may still list it, so filter defensively.
    if config.chat.tools and "complete" in config.chat.tools:
        config.chat.tools = [t for t in config.chat.tools if t != "complete"]

    try:
        init(
            config.chat.model,
            interactive=True,
            tool_allowlist=config.chat.tools,
            tool_format=config.chat.tool_format,
            no_confirm=no_confirm,
        )
    except (ValueError, Exception) as e:
        raise click.ClickException(str(e)) from e

    # only generate the (expensive) initial system prompt for new conversations
    log_file = logdir / "conversation.jsonl"
    is_existing = log_file.exists() and log_file.stat().st_size > 0
    initial_msgs = (
        []
        if is_existing
        else get_prompt(
            get_tools(),
            tool_format=config.chat.tool_format,
            interactive=True,
            model=config.chat.model,
            workspace=workspace_path,
        )
    )

    manager = LogManager.load(logdir, initial_msgs=initial_msgs, create=True)
    os.chdir(workspace_path)

    app = GptmeApp(
        manager,
        tool_format=config.chat.tool_format,
        workspace=workspace_path,
        auto_confirm=no_confirm,
        inline=inline,
        experimental_jelly_errors=experimental_jelly_errors,
    )
    if inline:
        _print_history(manager)
        # mouse=False leaves wheel scrolling to the terminal (native scrollback)
        app.run(inline=True, inline_no_clear=True, mouse=False)
    else:
        app.run()
    print(f"Conversation saved: {logdir.name}")
    print(f"Resume with: gptme-tui -n {logdir.name}  (or gptme -r in the CLI)")
    sys.exit(0)


if __name__ == "__main__":
    main()
