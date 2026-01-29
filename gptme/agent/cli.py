"""
CLI commands for gptme agent management.

Usage:
    gptme-agent status              # Show all agent statuses
    gptme-agent setup <path>        # Set up agent workspace (from template or scratch)
    gptme-agent install [--timer]   # Install systemd/launchd services
    gptme-agent start [<name>]      # Start agent(s)
    gptme-agent stop [<name>]       # Stop agent(s)
    gptme-agent logs [<name>]       # View agent logs
    gptme-agent run [<name>]        # Manual trigger (immediate run)
    gptme-agent list                # List configured agents
"""

import logging
import shlex
import sys
from pathlib import Path

import click

from .service import ServiceStatus, detect_service_manager, get_service_manager
from .workspace import (
    DEFAULT_TEMPLATE_BRANCH,
    DEFAULT_TEMPLATE_REPO,
    DetectedWorkspace,
    WorkspaceError,
    create_workspace_from_template,
    create_workspace_structure,
    detect_workspaces,
)
from .workspace import init_conversation as init_agent_conversation

logger = logging.getLogger(__name__)


def _print_status(status: ServiceStatus) -> None:
    """Print formatted status for an agent."""
    state_emoji = "🟢" if status.running else "⚪"
    enabled_str = "enabled" if status.enabled else "disabled"

    click.echo(f"{state_emoji} {status.name}")
    click.echo(
        f"   State: {'running' if status.running else 'stopped'} ({enabled_str})"
    )

    if status.pid:
        click.echo(f"   PID: {status.pid}")
    if status.last_run:
        click.echo(f"   Last run: {status.last_run}")
    if status.next_run:
        click.echo(f"   Next run: {status.next_run}")
    if status.exit_code is not None and status.exit_code != 0:
        click.echo(f"   Last exit code: {status.exit_code}")


def _print_workspace(workspace: DetectedWorkspace) -> None:
    """Print formatted info for a detected workspace."""
    emoji = "📦" if workspace.has_run_script else "📁"
    click.echo(f"{emoji} {workspace.name}")
    click.echo(f"   Path: {workspace.path}")
    if workspace.has_run_script:
        click.echo("   Ready: yes (has autonomous-run.sh)")
    else:
        click.echo("   Ready: no (missing autonomous-run.sh)")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def main(verbose: bool = False):
    """Manage gptme autonomous agents.

    This command helps you set up, install, and manage autonomous gptme agents
    that run on a schedule using your system's service manager (systemd on Linux,
    launchd on macOS).

    \b
    Quick start:
      gptme-agent setup ~/my-agent    # Set up a new agent workspace
      gptme-agent install             # Install services
      gptme-agent status              # Check status

    \b
    Common workflows:
      gptme-agent logs --follow       # Monitor agent activity
      gptme-agent run                 # Trigger an immediate run
      gptme-agent stop                # Pause scheduled runs
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)


@main.command("status")
@click.argument("name", required=False)
@click.option(
    "--all",
    "-a",
    "show_all",
    is_flag=True,
    help="Show all detected workspaces, including not installed",
)
def status_cmd(name: str | None, show_all: bool):
    """Show status of agent(s).

    If NAME is provided, shows status for that specific agent.
    Otherwise, shows status for all installed agents.

    Use --all to also show detected workspaces that haven't been installed yet.
    """
    manager = get_service_manager()

    # Get installed agents (if service manager available)
    installed_agents = manager.list_agents() if manager else []

    if name:
        # Show specific agent
        if manager:
            status = manager.status(name)
            if status:
                _print_status(status)
                return

        # Check if it's a detected but not installed workspace
        workspaces = detect_workspaces(installed_agents=installed_agents)
        for ws in workspaces:
            if ws.name == name:
                _print_workspace(ws)
                click.echo()
                click.echo(
                    "💡 To install: gptme-agent install --workspace " + str(ws.path)
                )
                return

        click.echo(f"Agent '{name}' not found")
        sys.exit(1)
    else:
        # Show all agents
        has_output = False

        # Show installed agents
        if installed_agents:
            click.echo(f"📋 Installed agents ({len(installed_agents)}):\n")
            for agent_name in installed_agents:
                if manager:
                    status = manager.status(agent_name)
                    if status:
                        _print_status(status)
                        click.echo()
            has_output = True

        # Show detected workspaces (if --all or no agents installed)
        if show_all or not installed_agents:
            workspaces = detect_workspaces(installed_agents=installed_agents)
            not_installed = [ws for ws in workspaces if not ws.installed]

            if not_installed:
                if has_output:
                    click.echo()
                click.echo(
                    f"📦 Detected workspaces ({len(not_installed)} not installed):\n"
                )
                for ws in not_installed:
                    _print_workspace(ws)
                    click.echo()
                has_output = True

        if not has_output:
            if not manager:
                click.echo(f"❌ No supported service manager found on {sys.platform}")
                click.echo("   Supported: systemd (Linux), launchd (macOS)")
            click.echo("No agents found")
            click.echo()
            click.echo("To set up a new agent:")
            click.echo("  gptme-agent setup <workspace-path>")


@main.command("list")
def list_cmd():
    """List all installed agents."""
    manager = get_service_manager()
    if not manager:
        click.echo(f"❌ No supported service manager found on {sys.platform}")
        sys.exit(1)

    agents = manager.list_agents()
    if not agents:
        click.echo("No agents installed")
        return

    click.echo(f"Installed agents ({len(agents)}):")
    for name in agents:
        click.echo(f"  - {name}")


@main.command("setup")
@click.argument("path", type=click.Path(exists=False))
@click.option("--name", "-n", help="Agent name (defaults to directory name)")
@click.option(
    "--template/--no-template",
    "-t/-T",
    default=True,
    help="Use template repository (default: yes)",
)
@click.option(
    "--template-repo",
    default=DEFAULT_TEMPLATE_REPO,
    help=f"Template repository URL (default: {DEFAULT_TEMPLATE_REPO})",
)
@click.option(
    "--template-branch",
    default=DEFAULT_TEMPLATE_BRANCH,
    help=f"Template branch (default: {DEFAULT_TEMPLATE_BRANCH})",
)
@click.option(
    "--init-conversation",
    is_flag=True,
    help="Initialize first conversation for the agent",
)
def setup_cmd(
    path: str,
    name: str | None,
    template: bool,
    template_repo: str,
    template_branch: str,
    init_conversation: bool,
):
    """Set up a new agent workspace.

    PATH is the directory where the agent workspace will be created.

    By default, this clones from gptme-agent-template and runs its fork.sh script
    to create a fully-featured agent workspace. Use --no-template for a minimal
    workspace without the template.

    \b
    Template-based setup (default):
    - Clones gptme-agent-template repository
    - Runs fork.sh to customize for your agent
    - Includes lessons, knowledge structure, and automation

    \b
    Minimal setup (--no-template):
    - Creates basic directory structure
    - Generates minimal gptme.toml
    - Creates autonomous run script

    \b
    Example:
      gptme-agent setup ~/my-agent                    # Template-based (recommended)
      gptme-agent setup ~/my-agent --name bob         # Custom agent name
      gptme-agent setup ~/my-agent --no-template      # Minimal setup
      gptme-agent setup ~/my-agent --init-conversation  # Also create first conversation
    """
    workspace = Path(path).expanduser().resolve()
    agent_name = name or workspace.name

    click.echo(f"🚀 Setting up agent workspace: {workspace}")
    click.echo(f"   Agent name: {agent_name}")
    click.echo(f"   Mode: {'template-based' if template else 'minimal'}")

    if workspace.exists():
        if not workspace.is_dir():
            click.echo(f"❌ Path exists and is not a directory: {workspace}")
            sys.exit(1)

        # Check if it looks like an existing workspace
        gptme_toml = workspace / "gptme.toml"
        if gptme_toml.exists():
            click.echo("✓ Existing workspace detected (has gptme.toml)")
            click.echo("   Use 'gptme-agent install' to install services")
            return

        if any(workspace.iterdir()):
            click.echo(f"❌ Directory is not empty: {workspace}")
            click.echo("   Please use an empty directory or remove existing files")
            sys.exit(1)

    click.echo()

    try:
        if template:
            # Template-based setup (recommended)
            click.echo(f"📦 Cloning template from {template_repo}...")
            click.echo(f"   Branch: {template_branch}")

            # The fork.sh in gptme-agent-template expects: ./fork.sh <path> <name>
            fork_command = f"./scripts/fork.sh {shlex.quote(str(workspace))} {shlex.quote(agent_name)}"

            create_workspace_from_template(
                path=workspace,
                agent_name=agent_name,
                template_repo=template_repo,
                template_branch=template_branch,
                fork_command=fork_command,
            )
            click.echo("✓ Template cloned and customized")
        else:
            # Minimal setup (fallback)
            click.echo("📁 Creating minimal workspace structure...")
            create_workspace_structure(workspace, agent_name)
            click.echo("✓ Created directory structure")
            click.echo("✓ Created autonomous run script")
            click.echo("✓ Created gptme.toml")
            click.echo("✓ Created README.md")

        # Optionally initialize first conversation
        if init_conversation:
            click.echo()
            click.echo("💬 Initializing first conversation...")
            conversation_id = init_agent_conversation(workspace)
            click.echo(f"✓ Created conversation: {conversation_id}")

    except WorkspaceError as e:
        click.echo(f"❌ Setup failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error during setup")
        click.echo(f"❌ Unexpected error: {e}")
        sys.exit(1)

    click.echo()
    click.echo("✅ Workspace setup complete!")
    click.echo()
    click.echo("Next steps:")
    click.echo(f"  1. cd {workspace}")
    click.echo("  2. Review and customize gptme.toml")
    click.echo("  3. gptme-agent install    # Install services")
    click.echo("  4. gptme-agent status     # Check status")


@main.command("install")
@click.option("--name", "-n", help="Agent name (defaults to current directory name)")
@click.option("--workspace", "-w", type=click.Path(exists=True), help="Workspace path")
@click.option(
    "--schedule",
    "-s",
    default="*:00/30",
    help="Schedule (systemd calendar format). Default: every 30 minutes",
)
def install_cmd(name: str | None, workspace: str | None, schedule: str):
    """Install agent services.

    Creates and enables systemd service/timer (Linux) or launchd plist (macOS)
    for the agent to run on the specified schedule.

    \b
    Schedule format (systemd OnCalendar):
      *:00/30     - Every 30 minutes
      *:00        - Every hour
      *-*-* 06:00 - Daily at 6 AM
      Mon *:00    - Every hour on Mondays

    \b
    Example:
      gptme-agent install                     # Install with defaults
      gptme-agent install --schedule "*:00"   # Every hour
    """
    manager = get_service_manager()
    if not manager:
        click.echo(f"❌ No supported service manager found on {sys.platform}")
        click.echo("   Supported: systemd (Linux), launchd (macOS)")
        sys.exit(1)

    ws_path = Path(workspace).resolve() if workspace else Path.cwd().resolve()
    agent_name = name or ws_path.name

    # Verify workspace looks valid
    run_script = ws_path / "scripts" / "runs" / "autonomous" / "autonomous-run.sh"
    if not run_script.exists():
        click.echo(f"❌ No autonomous run script found at {run_script}")
        click.echo("   Run 'gptme-agent setup' first to create the workspace structure")
        sys.exit(1)

    click.echo(f"📦 Installing agent '{agent_name}'")
    click.echo(f"   Workspace: {ws_path}")
    click.echo(f"   Schedule: {schedule}")
    click.echo(f"   Service manager: {detect_service_manager()}")
    click.echo()

    if manager.install(agent_name, ws_path, schedule=schedule):
        click.echo("✅ Agent installed successfully!")
        click.echo()
        click.echo("Commands:")
        click.echo(f"  gptme-agent status {agent_name}   # Check status")
        click.echo(f"  gptme-agent logs {agent_name}     # View logs")
        click.echo(f"  gptme-agent run {agent_name}      # Trigger immediate run")
    else:
        click.echo("❌ Installation failed")
        sys.exit(1)


@main.command("uninstall")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def uninstall_cmd(name: str, yes: bool):
    """Uninstall an agent's services.

    Removes the systemd/launchd service files for the specified agent.
    This does NOT delete the workspace directory.
    """
    manager = get_service_manager()
    if not manager:
        click.echo("❌ No supported service manager found")
        sys.exit(1)

    if not yes:
        if not click.confirm(f"Uninstall agent '{name}'?"):
            return

    if manager.uninstall(name):
        click.echo(f"✅ Agent '{name}' uninstalled")
    else:
        click.echo(f"❌ Failed to uninstall agent '{name}'")
        sys.exit(1)


@main.command("start")
@click.argument("name", required=False)
def start_cmd(name: str | None):
    """Start agent(s).

    Enables the timer/scheduler for the agent to run on schedule.
    If NAME is not provided, starts the agent in the current directory.
    """
    manager = get_service_manager()
    if not manager:
        click.echo("❌ No supported service manager found")
        sys.exit(1)

    agent_name = name or Path.cwd().name

    if manager.start(agent_name):
        click.echo(f"✅ Agent '{agent_name}' started")
    else:
        click.echo(f"❌ Failed to start agent '{agent_name}'")
        sys.exit(1)


@main.command("stop")
@click.argument("name", required=False)
def stop_cmd(name: str | None):
    """Stop agent(s).

    Disables the timer/scheduler to pause scheduled runs.
    If NAME is not provided, stops the agent in the current directory.
    """
    manager = get_service_manager()
    if not manager:
        click.echo("❌ No supported service manager found")
        sys.exit(1)

    agent_name = name or Path.cwd().name

    if manager.stop(agent_name):
        click.echo(f"✅ Agent '{agent_name}' stopped")
    else:
        click.echo(f"❌ Failed to stop agent '{agent_name}'")
        sys.exit(1)


@main.command("restart")
@click.argument("name", required=False)
def restart_cmd(name: str | None):
    """Restart agent(s).

    If NAME is not provided, restarts the agent in the current directory.
    """
    manager = get_service_manager()
    if not manager:
        click.echo("❌ No supported service manager found")
        sys.exit(1)

    agent_name = name or Path.cwd().name

    if manager.restart(agent_name):
        click.echo(f"✅ Agent '{agent_name}' restarted")
    else:
        click.echo(f"❌ Failed to restart agent '{agent_name}'")
        sys.exit(1)


@main.command("run")
@click.argument("name", required=False)
def run_cmd(name: str | None):
    """Trigger an immediate agent run.

    Starts a one-time execution of the agent's autonomous run script.
    This is useful for testing or manually triggering work.

    If NAME is not provided, runs the agent in the current directory.
    """
    manager = get_service_manager()
    if not manager:
        click.echo("❌ No supported service manager found")
        sys.exit(1)

    agent_name = name or Path.cwd().name

    click.echo(f"🚀 Triggering run for agent '{agent_name}'...")
    if manager.run(agent_name):
        click.echo("✅ Run triggered")
        click.echo()
        click.echo(f"View logs: gptme-agent logs {agent_name} --follow")
    else:
        click.echo("❌ Failed to trigger run")
        sys.exit(1)


@main.command("logs")
@click.argument("name", required=False)
@click.option("--lines", "-n", default=50, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
def logs_cmd(name: str | None, lines: int, follow: bool):
    """View agent logs.

    Shows recent log output from the agent's autonomous runs.

    If NAME is not provided, shows logs for the agent in the current directory.

    \b
    Example:
      gptme-agent logs              # Last 50 lines
      gptme-agent logs -n 100       # Last 100 lines
      gptme-agent logs -f           # Follow (live) output
    """
    manager = get_service_manager()
    if not manager:
        click.echo("❌ No supported service manager found")
        sys.exit(1)

    agent_name = name or Path.cwd().name

    # For follow mode, logs() will exec and not return
    output = manager.logs(agent_name, lines=lines, follow=follow)
    if output:
        click.echo(output)
    else:
        click.echo(f"No logs found for agent '{agent_name}'")


if __name__ == "__main__":
    main()
