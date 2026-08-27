"""gptme daemon — persistent background session management.

Commands:
  gptme daemon start [--session NAME] [PROMPT...]  Start a daemon session
  gptme daemon attach NAME                         Attach to a running daemon
  gptme daemon list                                List running daemons
  gptme daemon stop NAME                           Stop a daemon session
  gptme daemon status NAME                         Show daemon status

Design notes:
- attach auto-starts a daemon when the session does not yet exist
- Single-client MVP: only one attach at a time (Phase 3 adds multi-client)
- Non-interactive mode: prompts are passed as arguments, not typed interactively
  (PTY/interactive support is Phase 2)
"""

from __future__ import annotations

import logging

import click

logger = logging.getLogger(__name__)


@click.group("daemon")
def cli() -> None:
    """Manage persistent background gptme sessions."""


@cli.command("start")
@click.option(
    "--session",
    "-s",
    default=None,
    help="Session name (defaults to a timestamp-based name)",
)
@click.option("--model", "-m", default=None, help="Model to use")
@click.option("--workspace", "-w", default=None, help="Workspace directory")
@click.argument("prompts", nargs=-1)
def start(
    session: str | None,
    model: str | None,
    workspace: str | None,
    prompts: tuple[str, ...],
) -> None:
    """Start a gptme session as a background daemon.

    The session runs non-interactively in the background and writes to its
    conversation log. Attach later with 'gptme daemon attach NAME' to watch
    output or inject additional prompts.

    Example:

        gptme daemon start --session mywork "Implement feature X"
        gptme daemon attach mywork
    """
    from ..server.daemon import SessionDaemon, get_socket_path

    if session is None:
        from datetime import datetime, timezone

        session = datetime.now(timezone.utc).strftime("daemon-%Y%m%dT%H%M%S")

    sock_path = get_socket_path(session)
    if sock_path.exists():
        daemon = SessionDaemon(session)
        if daemon.is_running():
            click.echo(
                f"Daemon '{session}' is already running (socket: {sock_path})", err=True
            )
            raise SystemExit(1)

    # Build gptme args
    gptme_args: list[str] = ["--name", session]
    if model:
        gptme_args += ["--model", model]
    if workspace:
        gptme_args += ["--workspace", workspace]
    gptme_args += list(prompts)

    daemon = SessionDaemon(session)
    click.echo(f"Starting daemon '{session}'...")

    # daemonize=True → double-fork; parent returns here, child runs the daemon
    daemon.start(gptme_args, daemonize=True)

    # Parent continues here
    click.echo(f"Daemon started. Attach with: gptme daemon attach {session}")
    click.echo(f"Socket: {sock_path}")


@cli.command("attach")
@click.argument("session")
@click.option(
    "--start-if-missing",
    is_flag=True,
    default=True,
    show_default=True,
    help="Auto-start the daemon if the session is not running (default: on)",
)
def attach_cmd(session: str, start_if_missing: bool) -> None:
    """Attach to a running daemon session.

    Prints buffered output from session start, then relays stdin/stdout in
    real-time. Detach with Ctrl-D or Ctrl-C — the daemon keeps running.
    """
    from ..server.daemon import SessionDaemon, attach, get_socket_path

    sock_path = get_socket_path(session)
    if not sock_path.exists():
        if not start_if_missing:
            click.echo(
                f"No running daemon for session '{session}'. Start with: gptme daemon start --session {session}",
                err=True,
            )
            raise SystemExit(1)

        click.echo(f"Session '{session}' not running — starting it now...", err=True)
        daemon = SessionDaemon(session)
        daemon.start(["--name", session], daemonize=True)

        # Wait briefly for the socket to appear
        import time

        for _ in range(20):
            if sock_path.exists():
                break
            time.sleep(0.2)
        else:
            click.echo("Daemon did not start in time (socket not found).", err=True)
            raise SystemExit(1)

    try:
        attach(session)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1) from e
    except ConnectionRefusedError as e:
        click.echo(
            f"Could not connect to daemon '{session}'. It may have exited.", err=True
        )
        raise SystemExit(1) from e


@cli.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_cmd(as_json: bool) -> None:
    """List running daemon sessions."""
    from ..server.daemon import list_daemons

    daemons = list_daemons()
    if as_json:
        import json

        click.echo(json.dumps(daemons, indent=2))
        return

    if not daemons:
        click.echo("No daemon sessions found.")
        return

    click.echo(f"{'SESSION':<20} {'PID':<8} {'STATUS':<10} SOCKET")
    for d in daemons:
        pid = str(d["pid"]) if d["pid"] else "-"
        status = "running" if d["running"] else "stopped"
        sock = d["socket"] or "-"
        click.echo(f"{d['session']:<20} {pid:<8} {status:<10} {sock}")


@cli.command("stop")
@click.argument("session")
def stop_cmd(session: str) -> None:
    """Stop a running daemon session (sends SIGTERM)."""
    from ..server.daemon import SessionDaemon

    daemon = SessionDaemon(session)
    if not daemon.is_running():
        click.echo(f"Daemon '{session}' is not running.", err=True)
        raise SystemExit(1)

    daemon.stop()
    click.echo(f"Stopped daemon '{session}'.")


@cli.command("status")
@click.argument("session")
def status_cmd(session: str) -> None:
    """Show status of a daemon session."""
    from ..server.daemon import SessionDaemon, get_socket_path

    daemon = SessionDaemon(session)
    sock_path = get_socket_path(session)
    running = daemon.is_running()
    click.echo(f"Session:  {session}")
    click.echo(f"Status:   {'running' if running else 'stopped'}")
    click.echo(
        f"Socket:   {sock_path} ({'exists' if sock_path.exists() else 'missing'})"
    )


# Allow `gptme-daemon` as a standalone entry point
def main() -> None:
    cli()


if __name__ == "__main__":
    main()
