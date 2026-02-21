"""
Diagnostic command for gptme system health.

Usage:
    gptme-doctor              # Run all diagnostics
    gptme-doctor --verbose    # Include detailed output
    gptme-doctor --fix        # Attempt to fix issues (future)
"""

import importlib.util
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import config_path, get_config
from ..llm import list_available_providers
from ..llm.models import PROVIDERS
from ..llm.validate import OAUTH_PROVIDERS, PROVIDER_DOCS, validate_api_key

logger = logging.getLogger(__name__)
console = Console()


class CheckStatus(Enum):
    """Status of a diagnostic check."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    status: CheckStatus
    message: str
    details: str | None = None
    fix_hint: str | None = None


def _status_emoji(status: CheckStatus) -> str:
    """Get emoji for status."""
    return {
        CheckStatus.OK: "✅",
        CheckStatus.WARNING: "⚠️",
        CheckStatus.ERROR: "❌",
        CheckStatus.SKIPPED: "⏭️",
    }[status]


def _check_api_keys(verbose: bool = False) -> list[CheckResult]:
    """Check API key configuration and validity."""
    results = []

    # Get configured providers
    available_providers = list_available_providers()
    available_provider_map = dict(available_providers)
    config = get_config()

    # Special case env var names (only azure differs from the default pattern)
    special_env_vars = {
        "azure": "AZURE_OPENAI_API_KEY",
    }

    for provider in PROVIDERS:
        # Handle OAuth-based providers separately
        if provider in OAUTH_PROVIDERS:
            if provider in available_provider_map:
                results.append(
                    CheckResult(
                        name=f"Auth: {provider}",
                        status=CheckStatus.OK,
                        message="Authenticated (OAuth)",
                        details="Token file present" if verbose else None,
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name=f"Auth: {provider}",
                        status=CheckStatus.SKIPPED,
                        message="Not authenticated",
                        fix_hint=f"Run: gptme auth {provider}",
                    )
                )
            continue

        # Check if provider has API key configured
        if provider in available_provider_map:
            # Key is configured, validate it
            env_var = special_env_vars.get(provider, f"{provider.upper()}_API_KEY")

            # Try to get the API key
            api_key = os.environ.get(env_var) or config.get_env(env_var)

            if api_key:
                # Validate the key
                is_valid, error_msg = validate_api_key(api_key, provider)
                if is_valid:
                    results.append(
                        CheckResult(
                            name=f"API Key: {provider}",
                            status=CheckStatus.OK,
                            message="Configured and valid",
                            details=f"Key prefix: {api_key[:8]}..."
                            if verbose
                            else None,
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            name=f"API Key: {provider}",
                            status=CheckStatus.ERROR,
                            message=f"Invalid: {error_msg}",
                            fix_hint=f"Get a valid key at: {PROVIDER_DOCS.get(provider, 'provider docs')}",
                        )
                    )
            else:
                # Provider is marked available but we can't retrieve the key
                # This is a warning - the key may be configured differently
                results.append(
                    CheckResult(
                        name=f"API Key: {provider}",
                        status=CheckStatus.WARNING,
                        message="Provider available but key not retrievable for validation",
                        details=f"Expected env var: {env_var}" if verbose else None,
                    )
                )
        else:
            # No key configured - this is just informational
            results.append(
                CheckResult(
                    name=f"API Key: {provider}",
                    status=CheckStatus.SKIPPED,
                    message="Not configured",
                    fix_hint=f"Get a key at: {PROVIDER_DOCS.get(provider, 'provider docs')}",
                )
            )

    return results


def _check_tools(verbose: bool = False) -> list[CheckResult]:
    """Check required and optional tool dependencies."""
    results = []

    # Required tools
    required_tools = [
        ("python3", "Python interpreter"),
        ("git", "Version control"),
    ]

    # Optional tools with their purpose
    optional_tools = [
        ("gh", "GitHub CLI - enables GitHub integration"),
        ("tmux", "Terminal multiplexer - enables background tasks"),
        ("lynx", "Text browser - fallback for web browsing"),
        ("pdftotext", "PDF extraction - enables PDF reading"),
        ("rg", "ripgrep - fast file searching"),
        ("ast-grep", "AST-based code search"),
    ]

    # Check required tools
    for tool, desc in required_tools:
        path = shutil.which(tool)
        if path:
            results.append(
                CheckResult(
                    name=f"Tool: {tool}",
                    status=CheckStatus.OK,
                    message=desc,
                    details=path if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"Tool: {tool}",
                    status=CheckStatus.ERROR,
                    message=f"Not found - {desc}",
                    fix_hint=f"Install {tool} using your package manager",
                )
            )

    # Check optional tools
    for tool, desc in optional_tools:
        path = shutil.which(tool)
        if path:
            results.append(
                CheckResult(
                    name=f"Tool: {tool}",
                    status=CheckStatus.OK,
                    message=desc,
                    details=path if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"Tool: {tool}",
                    status=CheckStatus.WARNING,
                    message=f"Not found - {desc}",
                    fix_hint=f"Install {tool} for additional features",
                )
            )

    return results


def _check_python_deps(verbose: bool = False) -> list[CheckResult]:
    """Check Python optional dependencies."""
    results = []

    optional_deps = [
        ("playwright", "Browser automation for advanced web browsing"),
        ("sentence_transformers", "Embedding models for RAG and lessons"),
        ("torch", "PyTorch for ML features"),
    ]

    for dep, desc in optional_deps:
        spec = importlib.util.find_spec(dep)
        if spec:
            results.append(
                CheckResult(
                    name=f"Python: {dep}",
                    status=CheckStatus.OK,
                    message=desc,
                    details=str(spec.origin) if verbose and spec.origin else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"Python: {dep}",
                    status=CheckStatus.WARNING,
                    message=f"Not installed - {desc}",
                    fix_hint=f"pip install {dep}",
                )
            )

    return results


def _check_config(verbose: bool = False) -> list[CheckResult]:
    """Check configuration file status."""
    results = []

    # Check user config
    if Path(config_path).exists():
        results.append(
            CheckResult(
                name="Config: User",
                status=CheckStatus.OK,
                message="Found",
                details=config_path if verbose else None,
            )
        )
    else:
        results.append(
            CheckResult(
                name="Config: User",
                status=CheckStatus.WARNING,
                message="Not found (using defaults)",
                fix_hint=f"Create {config_path} to customize settings",
            )
        )

    # Check project config
    cwd = Path.cwd()
    project_config = cwd / "gptme.toml"
    github_config = cwd / ".github" / "gptme.toml"

    if project_config.exists():
        results.append(
            CheckResult(
                name="Config: Project",
                status=CheckStatus.OK,
                message="Found gptme.toml",
                details=str(project_config) if verbose else None,
            )
        )
    elif github_config.exists():
        results.append(
            CheckResult(
                name="Config: Project",
                status=CheckStatus.OK,
                message="Found .github/gptme.toml",
                details=str(github_config) if verbose else None,
            )
        )
    else:
        results.append(
            CheckResult(
                name="Config: Project",
                status=CheckStatus.SKIPPED,
                message="No project config in current directory",
                fix_hint="Create gptme.toml to configure this project",
            )
        )

    return results


def _check_permissions(verbose: bool = False) -> list[CheckResult]:
    """Check file and directory permissions."""
    results = []
    from ..dirs import get_logs_dir

    # Check logs directory
    logs_dir = get_logs_dir()
    if logs_dir.exists():
        if os.access(logs_dir, os.W_OK):
            results.append(
                CheckResult(
                    name="Permissions: Logs",
                    status=CheckStatus.OK,
                    message="Logs directory writable",
                    details=str(logs_dir) if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Permissions: Logs",
                    status=CheckStatus.ERROR,
                    message="Logs directory not writable",
                    fix_hint=f"Check permissions on {logs_dir}",
                )
            )
    else:
        # Will be created on first use
        results.append(
            CheckResult(
                name="Permissions: Logs",
                status=CheckStatus.OK,
                message="Logs directory will be created on first use",
            )
        )

    # Check config directory
    config_dir = Path(config_path).parent
    if config_dir.exists():
        if os.access(config_dir, os.W_OK):
            results.append(
                CheckResult(
                    name="Permissions: Config",
                    status=CheckStatus.OK,
                    message="Config directory writable",
                    details=str(config_dir) if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Permissions: Config",
                    status=CheckStatus.ERROR,
                    message="Config directory not writable",
                    fix_hint=f"Check permissions on {config_dir}",
                )
            )

    return results


def run_diagnostics(verbose: bool = False) -> tuple[list[CheckResult], dict]:
    """Run all diagnostic checks.

    Returns:
        Tuple of (results list, summary dict)
    """
    all_results: list[CheckResult] = []

    # Run all checks
    all_results.extend(_check_config(verbose))
    all_results.extend(_check_api_keys(verbose))
    all_results.extend(_check_tools(verbose))
    all_results.extend(_check_python_deps(verbose))
    all_results.extend(_check_permissions(verbose))

    # Calculate summary
    summary = {
        "total": len(all_results),
        "ok": sum(1 for r in all_results if r.status == CheckStatus.OK),
        "warning": sum(1 for r in all_results if r.status == CheckStatus.WARNING),
        "error": sum(1 for r in all_results if r.status == CheckStatus.ERROR),
        "skipped": sum(1 for r in all_results if r.status == CheckStatus.SKIPPED),
    }

    return all_results, summary


def print_results(results: list[CheckResult], summary: dict, verbose: bool = False):
    """Print diagnostic results in a formatted table."""
    console.print(
        Panel.fit(
            Text("🩺 gptme doctor", style="bold blue"),
            style="blue",
            padding=(0, 2),
        )
    )
    console.print()

    # Group results by category
    categories: dict[str, list[CheckResult]] = {}
    for result in results:
        category = result.name.split(":")[0]
        if category not in categories:
            categories[category] = []
        categories[category].append(result)

    # Print each category
    for category, cat_results in categories.items():
        table = Table(title=category, show_header=False, box=None, padding=(0, 1))
        table.add_column("Status", width=3)
        table.add_column("Name", style="cyan")
        table.add_column("Message")

        for result in cat_results:
            emoji = _status_emoji(result.status)
            name = result.name.split(": ", 1)[1] if ": " in result.name else result.name

            # Build message with optional details
            msg = result.message
            if verbose and result.details:
                msg += f"\n  [dim]{result.details}[/dim]"

            table.add_row(emoji, name, msg)

            # Show fix hints for errors/warnings
            if result.fix_hint and result.status in (
                CheckStatus.ERROR,
                CheckStatus.WARNING,
            ):
                if verbose or result.status == CheckStatus.ERROR:
                    table.add_row("", "", f"  [dim]→ {result.fix_hint}[/dim]")

        console.print(table)
        console.print()

    # Print summary
    status_line = []
    if summary["ok"] > 0:
        status_line.append(f"[green]{summary['ok']} passed[/green]")
    if summary["warning"] > 0:
        status_line.append(f"[yellow]{summary['warning']} warnings[/yellow]")
    if summary["error"] > 0:
        status_line.append(f"[red]{summary['error']} errors[/red]")
    if summary["skipped"] > 0:
        status_line.append(f"[dim]{summary['skipped']} skipped[/dim]")

    console.print(f"Summary: {', '.join(status_line)}")

    # Overall status
    if summary["error"] > 0:
        console.print("\n[red]❌ Some issues need attention[/red]")
        return 1
    if summary["warning"] > 0:
        console.print("\n[yellow]⚠️ System operational with some warnings[/yellow]")
        return 0
    console.print("\n[green]✅ All systems operational[/green]")
    return 0


@click.command()
@click.option("-v", "--verbose", is_flag=True, help="Show detailed output")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def main(verbose: bool = False, output_json: bool = False):
    """Run system diagnostics for gptme.

    Checks API keys, tools, dependencies, configuration, and permissions
    to identify any issues that might affect gptme operation.

    \b
    Examples:
        gptme-doctor              # Quick health check
        gptme-doctor --verbose    # Detailed output with paths and hints
        gptme-doctor --json       # Machine-readable output
    """
    results, summary = run_diagnostics(verbose)

    if output_json:
        import json

        output = {
            "summary": summary,
            "results": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "message": r.message,
                    "details": r.details,
                    "fix_hint": r.fix_hint,
                }
                for r in results
            ],
        }
        click.echo(json.dumps(output, indent=2))
        sys.exit(1 if summary["error"] > 0 else 0)
    else:
        exit_code = print_results(results, summary, verbose)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
