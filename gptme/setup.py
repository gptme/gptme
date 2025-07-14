"""Setup functionality for gptme configuration and completions."""

import importlib.util
import os
import shutil
from pathlib import Path
from typing import get_args

from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

import gptme

from .config import config_path, get_config, set_config_value
from .llm import get_model_from_api_key, list_available_providers
from .llm.models import Provider, get_default_model
from .util import console, path_with_tilde


def setup():
    """Setup gptme with completions, configuration, and project setup."""

    # 1. Show user configuration status
    _show_user_config_status()

    # 2. Project setup
    _setup_project()

    # 3. Optional dependencies
    _check_optional_dependencies()

    # 4. Pre-commit setup
    _suggest_precommit()

    # 5. Shell completions
    _setup_completions()

    console.print(
        Panel.fit(
            "[bold green]✅ Setup complete![/bold green]\n"
            "You can now use gptme with improved configuration.",
            style="green",
            padding=(0, 1),
        )
    )


def _detect_shell() -> str | None:
    """Detect the current shell."""
    shell = os.environ.get("SHELL", "").split("/")[-1]
    return shell if shell in ["fish", "bash", "zsh"] else None


def _is_wayland_environment() -> bool:
    """Detect if we're running in a Wayland environment."""
    return (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or os.environ.get("WAYLAND_DISPLAY") is not None
    )


def _setup_completions():
    """Setup shell completions."""
    shell = _detect_shell()
    if not shell:
        console.print("[red]❌ Could not detect shell type for completions[/red]")
        console.print()
        return

    if shell == "fish":
        fish_completions_dir = Path.home() / ".config" / "fish" / "completions"
        fish_completions_file = fish_completions_dir / "gptme.fish"

        # Find the gptme installation directory
        try:
            gptme_dir = Path(gptme.__file__).parent.parent
            source_file = gptme_dir / "scripts" / "completions" / "gptme.fish"

            if not source_file.exists():
                console.print(
                    f"[red]❌ Completions file not found at {source_file}[/red]"
                )
                console.print()
                return

            # Create completions directory if it doesn't exist
            fish_completions_dir.mkdir(parents=True, exist_ok=True)

            # Check if completions file exists and verify it's correct
            if fish_completions_file.exists():
                needed_update = _check_completions_file(
                    fish_completions_file, source_file
                )
                if not needed_update:
                    console.print(
                        f"[green]✅ Fish completions correctly installed[/green] [dim]({path_with_tilde(fish_completions_file)})[/dim]"
                    )
                    return
            else:
                _install_fish_completions(fish_completions_file, source_file)
                return

        except ImportError:
            console.print("[red]❌ Could not find gptme installation directory[/red]")

    elif shell in ["bash", "zsh"]:
        console.print(f"[blue]Detected shell:[/blue] [bold]{shell}[/bold]")
        console.print(f"[yellow]⚠️  {shell} completions not yet implemented[/yellow]")
        console.print("   [dim]Fish completions are currently supported[/dim]")

    console.print()


def _check_completions_file(completions_file: Path, source_file: Path) -> bool:
    """Check if completions file is correct and up-to-date. Returns True if update was recommended."""
    if completions_file.is_symlink():
        # Check if symlink points to the current source file
        current_target = completions_file.resolve()
        if current_target == source_file.resolve():
            return False
        else:
            console.print(
                "[yellow]⚠️  Fish completions symlink points to outdated location[/yellow]"
            )
            console.print(f"   Current: [dim]{current_target}[/dim]")
            console.print(f"   Expected: [dim]{source_file}[/dim]")

            if Confirm.ask(
                "Update completions to point to current installation?", default=True
            ):
                try:
                    completions_file.unlink()
                    _install_fish_completions(completions_file, source_file)
                except OSError as e:
                    console.print(f"[red]❌ Failed to update completions: {e}[/red]")
            else:
                console.print("[yellow]⚠️  Completions may be outdated[/yellow]")
            return True
    else:
        # Regular file - check if it's the same content or offer to replace with symlink
        console.print(
            f"[yellow]⚠️  Fish completions file exists but is not a symlink[/yellow] [dim]({path_with_tilde(completions_file)})[/dim]"
        )

        # Check if content matches
        try:
            if completions_file.read_text() == source_file.read_text():
                console.print("   [green]Content matches current version[/green]")
                if Confirm.ask(
                    "Replace with symlink to automatically stay updated?",
                    default=False,
                ):
                    completions_file.unlink()
                    _install_fish_completions(completions_file, source_file)
            else:
                console.print("   [red]Content differs from current version[/red]")
                if Confirm.ask("Replace with current version?", default=True):
                    completions_file.unlink()
                    _install_fish_completions(completions_file, source_file)
                return True
        except OSError:
            console.print("   [red]Could not read completions file[/red]")
            if Confirm.ask("Replace with current version?", default=True):
                completions_file.unlink()
                _install_fish_completions(completions_file, source_file)
            return True

        return False


def _install_fish_completions(fish_completions_file: Path, source_file: Path):
    console.print(
        Panel.fit(
            Text("🐚 Shell Completions", style="bold blue"),
            style="blue",
            padding=(0, 2),
        )
    )

    # TODO: prompt for confirmation?
    try:
        fish_completions_file.symlink_to(source_file)
        console.print(
            f"[green]✅ Fish completions installed[/green]\n"
            f"   [dim]{fish_completions_file}[/dim]"
        )
    except OSError:
        # Fallback to copy if symlink fails
        shutil.copy2(source_file, fish_completions_file)
        console.print(
            f"[green]✅ Fish completions installed[/green]\n"
            f"   [dim]{fish_completions_file}[/dim]"
        )

    console.print(
        "   [yellow]💡 Restart your shell or run 'exec fish' to enable completions[/yellow]"
    )


def _show_user_config_status():
    """Show current user configuration status."""
    config = get_config()

    # Show default model
    model_table = Table(show_header=False, box=None, padding=(0, 1))
    model_table.add_column("Property", style="cyan")
    model_table.add_column("Value", style="white")

    try:
        model = get_default_model()
        if model:
            model_table.add_row("Default model", f"[bold]{model.full}[/bold]")
            model_table.add_row("Provider", model.provider)
            model_table.add_row("Context", f"{model.context:,} tokens")
            model_table.add_row("Streaming", "✅" if model.supports_streaming else "❌")
            model_table.add_row("Vision", "✅" if model.supports_vision else "❌")
        else:
            model_table.add_row("Status", "[red]❌ No default model configured[/red]")
    except Exception as e:
        model_table.add_row("Status", f"[red]❌ Error getting default model: {e}[/red]")

    console.print(model_table)
    console.print()

    # Show configured providers (check for API keys)
    api_table = Table(title="API Keys Status", show_header=True, box=None)
    api_table.add_column("Provider", style="cyan", no_wrap=True)
    api_table.add_column("Status", justify="center")

    # Get all possible providers from the literal type
    all_providers = get_args(Provider)
    available_providers = list_available_providers()
    available_provider_names = {provider for provider, _ in available_providers}

    missing_providers = []
    for provider in all_providers:
        # Generate display name
        display_name = provider.replace("-", " ").title()
        if provider == "openai-azure":
            display_name = "Azure OpenAI"
        elif provider == "xai":
            display_name = "XAI"

        if provider in available_provider_names:
            api_table.add_row(display_name, "[green]✅[/green]")
        else:
            api_table.add_row(display_name, "[red]❌[/red]")
            missing_providers.append(display_name)

    console.print(api_table)

    # Offer to help set up missing API keys
    if missing_providers:
        console.print()
        if Confirm.ask("Would you like to set up an API key now?", default=False):
            try:
                provider, api_key = ask_for_api_key()
                console.print(
                    f"[green]✅ Successfully configured {provider} API key![/green]"
                )
                console.print(
                    "   [yellow]You may need to restart gptme for changes to take effect.[/yellow]"
                )
            except KeyboardInterrupt:
                console.print("\n[red]❌ API key setup cancelled.[/red]")
            except Exception as e:
                console.print(f"[red]❌ Error setting up API key: {e}[/red]")

    console.print()

    # Show extra features
    features_table = Table(title="Extra Features", show_header=True, box=None)
    features_table.add_column("Feature", style="cyan")
    features_table.add_column("Status", justify="center")

    features = {
        "GPTME_DING": "Bell sound on completion",
        "GPTME_CONTEXT_TREE": "Context tree visualization",
        "GPTME_AUTOCOMMIT": "Automatic git commits",
    }

    for env_var, description in features.items():
        enabled = config.get_env_bool(env_var, False)
        status = "[green]✅[/green]" if enabled else "[red]❌[/red]"
        features_table.add_row(description, status)

    console.print(features_table)
    console.print()

    if Confirm.ask("Would you like to configure extra features?", default=False):
        _configure_extra_features(features)

    console.print()


def _setup_project() -> bool:
    """Setup project configuration. Returns True if already configured."""
    cwd = Path.cwd()
    gptme_toml = cwd / "gptme.toml"
    github_gptme_toml = cwd / ".github" / "gptme.toml"

    is_configured = gptme_toml.exists() or github_gptme_toml.exists()

    if is_configured:
        # Show condensed status for already configured projects
        existing_file = gptme_toml if gptme_toml.exists() else github_gptme_toml
        console.print(
            f"[green]✅ Project Setup[/green] [dim]({existing_file.name})[/dim]"
        )
        return True

    # Show full setup panel for unconfigured projects
    console.print(
        Panel.fit(
            Text("📁 Project Setup", style="bold magenta"),
            style="magenta",
            padding=(0, 2),
        )
    )

    console.print("[yellow]No gptme.toml found in current directory[/yellow]")

    if Confirm.ask("Create a gptme.toml file for this project?", default=False):
        # Create basic gptme.toml
        config_content = """# gptme project configuration
# See https://gptme.org/docs/config.html for more options

prompt = "This is my project"

# Files to include in context by default
files = ["README.md"]

# Uncomment to enable RAG (Retrieval-Augmented Generation)
# [rag]
# enabled = true
"""

        gptme_toml.write_text(config_content)
        console.print(f"[green]✅ Created {gptme_toml}[/green]")
        console.print(
            "   [dim]Edit this file to customize your project's gptme configuration[/dim]"
        )

        # Show a preview of the created config
        console.print("\n[bold]Preview of created `gptme.toml`:[/bold]")
        console.print(
            Syntax(config_content, "toml", theme="monokai", line_numbers=True),
        )

    console.print()
    return False


def _suggest_precommit() -> bool:
    """Suggest setting up pre-commit. Returns True if already configured."""
    # TODO: also check
    cwd = Path.cwd()
    precommit_config = cwd / ".pre-commit-config.yaml"

    if precommit_config.exists():
        # Show condensed status for already configured pre-commit
        console.print(
            "[green]✅ Pre-commit Setup[/green] [dim](.pre-commit-config.yaml)[/dim]"
        )
        return True

    # Check if this looks like a Python project
    has_python_files = any(cwd.glob("*.py")) or any(cwd.glob("**/*.py"))
    has_git = (cwd / ".git").exists()

    if not has_git:
        console.print(
            "[blue]ℹ️  Pre-commit Setup[/blue] [dim](not a git repository)[/dim]"
        )
        console.print()
        return False

    if not has_python_files:
        console.print(
            "[blue]ℹ️  Pre-commit Setup[/blue] [dim](no Python files detected)[/dim]"
        )
        console.print()
        return False

    # Show full setup panel for unconfigured pre-commit
    console.print(
        Panel.fit(
            Text("🔍 Pre-commit Setup", style="bold yellow"),
            style="yellow",
            padding=(0, 2),
        )
    )

    console.print(
        "[green]This appears to be a Python project in a git repository[/green]"
    )

    if Confirm.ask("Would you like help setting up pre-commit hooks?", default=False):
        console.print()
        console.print(
            Panel.fit(
                "[bold]💡 You can ask gptme to help you set up pre-commit:[/bold]\n\n"
                "[cyan]Examples:[/cyan]\n"
                "• [dim]'Set up pre-commit with ruff, mypy, and black'[/dim]\n"
                "• [dim]'Add pre-commit hooks for Python linting and formatting'[/dim]\n"
                "• [dim]'Configure pre-commit for this project'[/dim]",
                title="💡 Suggestion",
                border_style="blue",
            )
        )

    console.print()
    return False


def _configure_extra_features(features: dict[str, str]):
    """Configure extra features interactively."""
    console.print(
        Panel.fit(
            Text("🔧 Configure Extra Features", style="bold cyan"),
            style="cyan",
            padding=(0, 2),
        )
    )

    config = get_config()
    changes_made = False

    for env_var, description in features.items():
        current_enabled = config.get_env_bool(env_var, False)
        status = "[green]enabled[/green]" if current_enabled else "[red]disabled[/red]"

        console.print(f"\n[bold]{description}[/bold]")
        console.print(f"  Currently: {status}")

        if Confirm.ask(f"  Enable {description.lower()}?", default=current_enabled):
            if not current_enabled:
                set_config_value(f"env.{env_var}", "1")
                console.print(f"  [green]✅ Enabled {description.lower()}[/green]")
                changes_made = True
            else:
                console.print("  [blue]ℹ️  Already enabled[/blue]")
        else:
            if current_enabled:
                set_config_value(f"env.{env_var}", "0")
                console.print(f"  [red]❌ Disabled {description.lower()}[/red]")
                changes_made = True
            else:
                console.print("  [blue]ℹ️  Remains disabled[/blue]")

    console.print()
    if changes_made:
        console.print(
            Panel.fit(
                f"[green]✅ Configuration saved to[/green] [dim]{config_path}[/dim]\n"
                "[yellow]Changes will take effect for new gptme sessions[/yellow]",
                border_style="green",
            )
        )
    else:
        console.print("[blue]ℹ️  No changes made[/blue]")


def _check_optional_dependencies():
    """Check for optional dependencies and show their status."""
    # Define optional dependencies with their purpose and installation instructions
    dependencies = [
        {
            "name": "playwright",
            "check_type": "python",
            "purpose": "Advanced web browsing with browser automation",
            "install": "pip install playwright && playwright install",
        },
        {
            "name": "lynx",
            "check_type": "command",
            "purpose": "Basic web browsing (fallback for browser tool)",
            "install": "brew install lynx  # macOS\nsudo apt install lynx  # Ubuntu/Debian",
        },
        {
            "name": "pdftotext",
            "check_type": "command",
            "purpose": "PDF text extraction",
            "install": "brew install poppler  # macOS\nsudo apt install poppler-utils  # Ubuntu/Debian",
        },
        {
            "name": "gh",
            "check_type": "command",
            "purpose": "GitHub CLI operations",
            "install": "brew install gh  # macOS\nsudo apt install gh  # Ubuntu/Debian",
        },
        {
            "name": "tmux",
            "check_type": "command",
            "purpose": "Terminal multiplexing for long-running processes",
            "install": "brew install tmux  # macOS\nsudo apt install tmux  # Ubuntu/Debian",
        },
    ]

    # Only check wl-clipboard in Wayland environments
    if _is_wayland_environment():
        dependencies.append(
            {
                "name": "wl-clipboard",
                "check_type": "command",
                "purpose": "Clipboard operations on Wayland",
                "install": "sudo apt install wl-clipboard  # Ubuntu/Debian",
            }
        )

    deps_table = Table(show_header=True, box=None)
    deps_table.add_column("Dependency", style="cyan", no_wrap=True)
    deps_table.add_column("Status", justify="center", width=8)
    deps_table.add_column("Purpose", style="dim")

    missing_deps = []

    for dep in dependencies:
        is_available = _check_dependency(dep["name"], dep["check_type"])
        status = "[green]✅[/green]" if is_available else "[red]❌[/red]"
        deps_table.add_row(dep["name"], status, dep["purpose"])

        if not is_available:
            missing_deps.append(dep)

    if missing_deps:
        console.print(
            Panel.fit(
                Text("📦 Optional Dependencies", style="bold purple"),
                style="purple",
                padding=(0, 2),
            )
        )

        console.print(deps_table)
        console.print(
            f"\n[yellow]💡 {len(missing_deps)} optional dependencies are missing.[/yellow]"
        )
        console.print(
            "[dim]These are not required but enable additional features.[/dim]"
        )

        # Show installation instructions for missing dependencies
        if Confirm.ask(
            "\nShow installation instructions for missing dependencies?", default=False
        ):
            install_table = Table(
                title="Installation Instructions", show_header=True, box=None
            )
            install_table.add_column("Dependency", style="cyan")
            install_table.add_column("Install Command", style="green")

            for dep in missing_deps:
                install_table.add_row(dep["name"], dep["install"])

            console.print()
            console.print(install_table)
    else:
        console.print("[green]✅ All optional dependencies are installed![/green]")


def _check_dependency(name: str, check_type: str) -> bool:
    """Check if a dependency is available."""
    if check_type == "command":
        return shutil.which(name) is not None
    elif check_type == "python":
        try:
            return importlib.util.find_spec(name) is not None
        except ImportError:
            return False
    return False


def _prompt_api_key() -> tuple[str, str, str]:  # pragma: no cover
    """Prompt user for API key and validate it."""
    console.print("Paste your API key [dim](We will auto-detect the provider)[/dim]")
    api_key = Prompt.ask("API key", password=True).strip()
    if (found_model_tuple := get_model_from_api_key(api_key)) is not None:
        return found_model_tuple
    else:
        console.print("[red]Invalid API key format. Please try again.[/red]")
        return _prompt_api_key()


def ask_for_api_key():  # pragma: no cover
    """Interactively ask user for API key."""
    console.print(
        Panel.fit(
            Text("🔑 API Key Setup", style="bold green"),
            style="green",
            padding=(0, 2),
        )
    )

    # Create a nice table with provider links
    providers_table = Table(title="🔑 Get API Keys", show_header=True, box=None)
    providers_table.add_column("Provider", style="cyan")
    providers_table.add_column("URL", style="blue")

    providers_table.add_row("OpenAI", "https://platform.openai.com/account/api-keys")
    providers_table.add_row("Anthropic", "https://console.anthropic.com/settings/keys")
    providers_table.add_row("OpenRouter", "https://openrouter.ai/settings/keys")
    providers_table.add_row("Gemini", "https://aistudio.google.com/app/apikey")

    console.print()
    console.print(providers_table)
    console.print()

    # Save to config
    api_key, provider, env_var = _prompt_api_key()
    set_config_value(f"env.{env_var}", api_key)

    console.print(
        Panel.fit(
            f"[green]✅ Successfully set up {provider} API key![/green]\n"
            f"[dim]API key saved to config at {config_path}[/dim]",
            border_style="green",
        )
    )

    return provider, api_key
