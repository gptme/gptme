"""
Diagnostic command for gptme system health.

Usage:
    gptme-doctor              # Run all diagnostics
    gptme-doctor --verbose    # Include detailed output
    gptme-doctor --fix        # Interactively repair provider setup
"""

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..__version__ import __version__
from ..config import MCPServerConfig, config_path, get_config, resolve_model_source
from ..info import get_config_info, get_installed_extras
from ..llm import PROVIDER_API_KEYS, is_plugin_provider, list_available_providers
from ..llm.models import PROVIDERS, is_custom_provider
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
                if is_valid and not error_msg:
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
                elif is_valid and error_msg:
                    results.append(
                        CheckResult(
                            name=f"API Key: {provider}",
                            status=CheckStatus.WARNING,
                            message=error_msg,
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


def _check_default_model(verbose: bool = False) -> list[CheckResult]:
    """Check that the selected model routes through an available provider."""
    config = get_config()
    available = [str(provider) for provider, _ in list_available_providers()]
    resolution = resolve_model_source(config)

    if resolution is None:
        if available:
            return [
                CheckResult(
                    name="Model: Default",
                    status=CheckStatus.OK,
                    message=f"Auto-detected provider: {available[0]}",
                    details="No explicit default model configured" if verbose else None,
                )
            ]
        return [
            CheckResult(
                name="Model: Default",
                status=CheckStatus.ERROR,
                message="No model or provider configured",
                fix_hint="Run: gptme-doctor --fix",
            )
        ]

    model, source = resolution
    provider = model.split("/", 1)[0] if "/" in model else model
    if provider in available:
        return [
            CheckResult(
                name="Model: Default",
                status=CheckStatus.OK,
                message=f"{model} ({source})",
            )
        ]

    if "/" not in model:
        return [
            CheckResult(
                name="Model: Default",
                status=CheckStatus.WARNING,
                message=f"Could not verify provider for unqualified model: {model}",
                details=f"Configured via {source}" if verbose else None,
            )
        ]

    observable_providers = set(PROVIDER_API_KEYS) | OAUTH_PROVIDERS
    if provider in observable_providers:
        return [
            CheckResult(
                name="Model: Default",
                status=CheckStatus.ERROR,
                message=f"Provider '{provider}' is not configured",
                details=f"Configured via {source}: {model}" if verbose else None,
                fix_hint="Run: gptme-doctor --fix",
            )
        ]

    if (
        provider in PROVIDERS
        or is_custom_provider(provider)
        or is_plugin_provider(provider)
    ):
        return [
            CheckResult(
                name="Model: Default",
                status=CheckStatus.WARNING,
                message=f"Could not verify provider authentication for: {model}",
                details=f"Configured via {source}" if verbose else None,
            )
        ]

    return [
        CheckResult(
            name="Model: Default",
            status=CheckStatus.ERROR,
            message=f"Unknown provider '{provider}' in configured model",
            details=f"Configured via {source}: {model}" if verbose else None,
            fix_hint="Run: gptme-doctor --fix",
        )
    ]


def _provider_repair_needed(results: list[CheckResult]) -> bool:
    """Return whether provider setup blocks a usable default model."""
    auth_results = [
        result for result in results if result.name.startswith(("API Key: ", "Auth: "))
    ]
    has_working_auth = any(
        result.status in (CheckStatus.OK, CheckStatus.WARNING)
        for result in auth_results
    )
    rejected_providers = set()
    for result in auth_results:
        if result.status != CheckStatus.ERROR:
            continue
        for prefix in ("API Key: ", "Auth: "):
            if result.name.startswith(prefix):
                rejected_providers.add(result.name.removeprefix(prefix))
                break
    has_broken_default = any(
        result.name == "Model: Default" and result.status == CheckStatus.ERROR
        for result in results
    )
    model_result = next(
        (result for result in results if result.name == "Model: Default"), None
    )
    selected_provider = None
    if model_result and model_result.status == CheckStatus.OK:
        if model_result.message.startswith("Auto-detected provider: "):
            selected_provider = model_result.message.removeprefix(
                "Auto-detected provider: "
            )
        elif "/" in model_result.message:
            selected_provider = model_result.message.split("/", 1)[0]
    has_rejected_default = selected_provider in rejected_providers
    has_configured_model = any(
        result.name == "Model: Default"
        and result.status in (CheckStatus.OK, CheckStatus.WARNING)
        for result in results
    )
    return (
        has_broken_default
        or has_rejected_default
        or (not has_working_auth and not has_configured_model)
    )


def _subscription_default_candidate(
    results: list[CheckResult],
) -> Literal["openai-subscription", "grok-subscription"] | None:
    """Choose an existing subscription when only the selected model is broken."""
    if not any(
        result.name == "Model: Default" and result.status == CheckStatus.ERROR
        for result in results
    ):
        return None

    available = {str(provider) for provider, _ in list_available_providers()}
    if "openai-subscription" in available:
        return "openai-subscription"
    if "grok-subscription" in available:
        return "grok-subscription"
    return None


def _is_interactive_terminal() -> bool:
    """Return whether provider repair can safely prompt and mutate config."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _validate_oauth_for_repair(results: list[CheckResult]) -> None:
    """Validate or refresh OAuth credentials before interactive repair routing."""
    for result in results:
        if not result.name.startswith("Auth: ") or result.status != CheckStatus.OK:
            continue

        provider = result.name.removeprefix("Auth: ")
        try:
            if provider == "openai-subscription":
                from ..llm.llm_openai_subscription import get_auth as get_openai_auth

                get_openai_auth(timeout=5)
            elif provider == "grok-subscription":
                from ..llm.llm_grok_subscription import get_auth as get_grok_auth

                get_grok_auth(timeout=5)
            else:
                continue
        except Exception as exc:
            result.status = CheckStatus.ERROR
            result.message = f"Authentication failed: {str(exc).splitlines()[0]}"
            result.fix_hint = f"Re-authenticate with: gptme-auth {provider}"
        else:
            result.message = "Authenticated (OAuth token valid)"


def _model_override_blocking_repair() -> str | None:
    """Return a higher-precedence model source that user config cannot replace."""
    resolution = resolve_model_source(get_config())
    if resolution is not None and resolution[1] != "models.default":
        return resolution[1]
    return None


def _check_tools(verbose: bool = False) -> list[CheckResult]:
    """Check required and optional tool dependencies."""
    results = []

    # Required tools
    required_tools = [
        ("python3", "Python interpreter"),
        ("git", "Version control"),
    ]

    # Optional tools: (name, description, fix_hint or None for generic)
    optional_tools: list[tuple[str, str, str | None]] = [
        ("gh", "GitHub CLI - enables GitHub integration", None),
        ("tmux", "Terminal multiplexer - enables background tasks", None),
        (
            "node",
            "Node.js - required for npx-based MCP servers",
            "Install from https://nodejs.org or via your package manager",
        ),
        (
            "npx",
            "npm package runner - runs MCP servers without install",
            "Usually bundled with Node.js; on Debian/Ubuntu install `npm` separately",
        ),
        (
            "docker",
            "Docker - enables eval sandbox and containerized tasks",
            "Install from https://docs.docker.com/get-docker/",
        ),
        ("lynx", "Text browser - fallback for web browsing", None),
        ("pdftotext", "PDF extraction - enables PDF reading", None),
        (
            "pdftoppm",
            "PDF page rasterizer - enables PDF preview in browser tool",
            "Usually provided by the poppler-utils package",
        ),
        (
            "pandoc",
            "Document converter - enables HTML→Markdown for browser tool",
            "Install from https://pandoc.org/installing.html or your package manager",
        ),
        (
            "convert",
            "ImageMagick - image conversion for browser and computer tools",
            "Install ImageMagick from https://imagemagick.org",
        ),
        (
            "vips",
            "libvips - fast image processing, PDF rasterization fallback for browser tool",
            "Install with: sudo apt install libvips-tools  or  brew install vips",
        ),
        ("rg", "ripgrep - fast file searching", None),
        ("ast-grep", "AST-based code search", None),
        (
            "shellcheck",
            "Shell script linter - enables shell command validation",
            "Install from https://www.shellcheck.net",
        ),
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
    for tool, desc, fix_hint in optional_tools:
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
                    fix_hint=fix_hint or f"Install {tool} for additional features",
                )
            )

    return results


def _check_python_deps(verbose: bool = False) -> list[CheckResult]:
    """Check Python optional dependencies (extras)."""
    results = []

    # Use shared info module to get extras status
    extras = get_installed_extras()

    for extra in extras:
        if extra.installed:
            results.append(
                CheckResult(
                    name=f"Python: {extra.name}",
                    status=CheckStatus.OK,
                    message=extra.description,
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"Python: {extra.name}",
                    status=CheckStatus.SKIPPED,
                    message=f"Not installed - {extra.description}",
                    fix_hint=f"pip install 'gptme[{extra.name}]'",
                )
            )

    return results


def _check_config(verbose: bool = False) -> list[CheckResult]:
    """Check configuration file status."""
    results = []

    # Use shared info module
    config_info = get_config_info()

    # Check user config
    if config_info["config_exists"]:
        results.append(
            CheckResult(
                name="Config: User",
                status=CheckStatus.OK,
                message="Found",
                details=config_info["config_path"] if verbose else None,
            )
        )
    else:
        results.append(
            CheckResult(
                name="Config: User",
                status=CheckStatus.WARNING,
                message="Not found (using defaults)",
                fix_hint=f"Create {config_info['config_path']} to customize settings",
            )
        )

    # Check project config
    if "project_config" in config_info:
        project_path = config_info["project_config"]
        name = Path(project_path).name
        if ".github" in project_path:
            name = ".github/gptme.toml"
        results.append(
            CheckResult(
                name="Config: Project",
                status=CheckStatus.OK,
                message=f"Found {name}",
                details=project_path if verbose else None,
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


def _check_proxy(verbose: bool = False) -> list[CheckResult]:
    """Check proxy URL configuration."""
    from urllib.parse import urlparse

    config = get_config()
    proxy_url = config.get_env("LLM_PROXY_URL", None)

    if not proxy_url:
        return [
            CheckResult(
                name="Proxy: LLM_PROXY_URL",
                status=CheckStatus.SKIPPED,
                message="Not configured",
                details="Set LLM_PROXY_URL to route LLM requests through a proxy"
                if verbose
                else None,
            )
        ]

    results = []
    try:
        parsed = urlparse(proxy_url)
        hostname = parsed.hostname
        # Accessing port validates malformed/non-numeric/out-of-range values.
        port = parsed.port
    except ValueError:
        results.append(
            CheckResult(
                name="Proxy: LLM_PROXY_URL",
                status=CheckStatus.ERROR,
                message="Malformed URL",
                details=None,
                fix_hint="Set LLM_PROXY_URL to a valid http or https URL",
            )
        )
        return results

    # Scheme must be http or https
    if parsed.scheme not in ("http", "https"):
        results.append(
            CheckResult(
                name="Proxy: LLM_PROXY_URL",
                status=CheckStatus.ERROR,
                message=f"Invalid scheme {parsed.scheme!r} — must be http or https",
                details=None,
                fix_hint="Set LLM_PROXY_URL to a URL starting with http:// or https://",
            )
        )
        return results

    # Host must be non-empty
    if not hostname:
        results.append(
            CheckResult(
                name="Proxy: LLM_PROXY_URL",
                status=CheckStatus.ERROR,
                message="Missing host",
                details=None,
                fix_hint="Set LLM_PROXY_URL to a full URL, e.g. https://my-proxy.example.com",
            )
        )
        return results

    display_host = hostname
    if ":" in display_host:
        display_host = f"[{display_host}]"
    if port is not None:
        display_host = f"{display_host}:{port}"

    # Path should be empty or just "/" — the Anthropic SDK appends its own paths.
    # Do not include it in diagnostics because proxy paths may contain credentials.
    if parsed.path and parsed.path != "/":
        results.append(
            CheckResult(
                name="Proxy: LLM_PROXY_URL",
                status=CheckStatus.WARNING,
                message="URL has a non-root path — may conflict with SDK routing",
                details=f"Proxy: {parsed.scheme}://{display_host}/[path redacted]"
                if verbose
                else None,
                fix_hint="Consider removing the path component; the Anthropic SDK appends its own paths",
            )
        )
    else:
        results.append(
            CheckResult(
                name="Proxy: LLM_PROXY_URL",
                status=CheckStatus.OK,
                message=f"Configured ({display_host})",
                details=f"Proxy: {parsed.scheme}://{display_host}" if verbose else None,
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


def _check_python_version(verbose: bool = False) -> list[CheckResult]:
    """Check that Python version meets gptme's minimum requirement (3.10+)."""
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"

    if version >= (3, 10):
        return [
            CheckResult(
                name="Version: Python",
                status=CheckStatus.OK,
                message=f"Python {version_str}",
                details=sys.executable if verbose else None,
            )
        ]
    return [
        CheckResult(
            name="Version: Python",
            status=CheckStatus.ERROR,
            message=f"Python {version_str} is below minimum (3.10)",
            fix_hint="Upgrade to Python 3.10 or newer",
        )
    ]


def _check_version(verbose: bool = False) -> list[CheckResult]:
    """Check if gptme is up to date by comparing with PyPI."""
    results = []

    try:
        from importlib.metadata import version as get_version

        current = get_version("gptme")
    except Exception:
        current = __version__

    # Skip version check for dev/editable installs
    if ".dev" in current or "+g" in current:
        results.append(
            CheckResult(
                name="Version: gptme",
                status=CheckStatus.OK,
                message=f"Development install ({current})",
                details="Skipping PyPI check for dev installs" if verbose else None,
            )
        )
        return results

    try:
        import json
        import urllib.request

        url = "https://pypi.org/pypi/gptme/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            latest = data["info"]["version"]

        if current == latest:
            results.append(
                CheckResult(
                    name="Version: gptme",
                    status=CheckStatus.OK,
                    message=f"Up to date ({current})",
                )
            )
        else:
            # Compare versions to determine severity
            from packaging.version import Version

            try:
                current_v = Version(current)
                latest_v = Version(latest)
                if latest_v <= current_v:
                    # User is running a newer version than PyPI (pre-release, branch install)
                    results.append(
                        CheckResult(
                            name="Version: gptme",
                            status=CheckStatus.OK,
                            message=f"Installed {current} (ahead of PyPI: {latest})",
                        )
                    )
                else:
                    is_major = latest_v.major > current_v.major
                    is_minor = latest_v.minor > current_v.minor
                    if is_major or is_minor:
                        results.append(
                            CheckResult(
                                name="Version: gptme",
                                status=CheckStatus.WARNING,
                                message=f"Update available: {current} → {latest}",
                                fix_hint="pip install --upgrade gptme",
                            )
                        )
                    else:
                        results.append(
                            CheckResult(
                                name="Version: gptme",
                                status=CheckStatus.OK,
                                message=f"Installed {current} (latest: {latest})",
                                details="Patch update available" if verbose else None,
                            )
                        )
            except Exception:
                results.append(
                    CheckResult(
                        name="Version: gptme",
                        status=CheckStatus.WARNING,
                        message=f"Update available: {current} → {latest}",
                        fix_hint="pip install --upgrade gptme",
                    )
                )
    except Exception as e:
        results.append(
            CheckResult(
                name="Version: gptme",
                status=CheckStatus.OK,
                message=f"Installed ({current})",
                details=f"Could not check PyPI: {e}" if verbose else None,
            )
        )

    return results


def _check_computer(verbose: bool = False) -> list[CheckResult]:
    """Check computer tool prerequisites (xdotool on Linux, cliclick on macOS)."""
    results: list[CheckResult] = []

    if sys.platform == "darwin":
        # screencapture should always be present on macOS — verify for consistency
        screencapture_path = shutil.which("screencapture")
        if screencapture_path:
            results.append(
                CheckResult(
                    name="Computer: screencapture",
                    status=CheckStatus.OK,
                    message="macOS screenshot tool (built-in)",
                    details=screencapture_path if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Computer: screencapture",
                    status=CheckStatus.WARNING,
                    message="Not found — screencapture should be built-in on macOS",
                )
            )
        # cliclick is required for mouse/keyboard control on macOS
        cliclick_path = shutil.which("cliclick")
        if cliclick_path:
            results.append(
                CheckResult(
                    name="Computer: cliclick",
                    status=CheckStatus.OK,
                    message="macOS mouse/keyboard control",
                    details=cliclick_path if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Computer: cliclick",
                    status=CheckStatus.WARNING,
                    message="Not found — required for mouse/keyboard control on macOS",
                    fix_hint="brew install cliclick",
                )
            )
    elif sys.platform.startswith("linux"):
        # Linux: X11 tools
        xdotool_path = shutil.which("xdotool")
        if xdotool_path:
            results.append(
                CheckResult(
                    name="Computer: xdotool",
                    status=CheckStatus.OK,
                    message="X11 mouse/keyboard automation",
                    details=xdotool_path if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Computer: xdotool",
                    status=CheckStatus.WARNING,
                    message="Not found — required for computer tool on Linux/X11",
                    fix_hint="sudo apt install xdotool  or  sudo pacman -S xdotool",
                )
            )

        scrot_path = shutil.which("scrot")
        if scrot_path:
            results.append(
                CheckResult(
                    name="Computer: scrot",
                    status=CheckStatus.OK,
                    message="X11 screenshot tool",
                    details=scrot_path if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Computer: scrot",
                    status=CheckStatus.WARNING,
                    message="Not found — screenshot fallback (gnome-screenshot also works)",
                    fix_hint="sudo apt install scrot  or  sudo pacman -S scrot",
                )
            )

        # Check DISPLAY
        display = os.environ.get("DISPLAY")
        if display:
            # Verify the X server at DISPLAY is actually reachable
            xdpyinfo_path = shutil.which("xdpyinfo")
            if xdpyinfo_path:
                try:
                    env = os.environ.copy()
                    env["DISPLAY"] = display
                    subprocess.run(
                        [xdpyinfo_path],
                        env=env,
                        capture_output=True,
                        check=True,
                        timeout=3,
                    )
                    results.append(
                        CheckResult(
                            name="Computer: DISPLAY",
                            status=CheckStatus.OK,
                            message=f"X11 display reachable ({display})",
                        )
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    results.append(
                        CheckResult(
                            name="Computer: DISPLAY",
                            status=CheckStatus.WARNING,
                            message=f"DISPLAY={display} is set but X server is not responding",
                            fix_hint=(
                                "Start an X server first:\n"
                                "  Xvfb :1 -screen 0 1024x768x24 &\n"
                                "  export DISPLAY=:1\n"
                                "Or use: xvfb-run gptme ..."
                            ),
                        )
                    )
            else:
                results.append(
                    CheckResult(
                        name="Computer: DISPLAY",
                        status=CheckStatus.OK,
                        message=f"X11 display set ({display}) — install xdpyinfo to verify it's reachable",
                    )
                )
        else:
            results.append(
                CheckResult(
                    name="Computer: DISPLAY",
                    status=CheckStatus.WARNING,
                    message="DISPLAY not set — computer tool requires an X11 display",
                    fix_hint="export DISPLAY=:1  or run inside Xvfb: xvfb-run gptme ...",
                )
            )

        # Check for a running window manager (EWMH: _NET_SUPPORTING_WM_CHECK on root window)
        xprop_path = shutil.which("xprop")
        if display and xprop_path:
            try:
                env = os.environ.copy()
                env["DISPLAY"] = display
                result_proc = subprocess.run(
                    [xprop_path, "-root", "_NET_SUPPORTING_WM_CHECK"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                if (
                    result_proc.returncode == 0
                    and "_NET_SUPPORTING_WM_CHECK" in result_proc.stdout
                ):
                    results.append(
                        CheckResult(
                            name="Computer: window manager",
                            status=CheckStatus.OK,
                            message="EWMH-compliant window manager detected",
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            name="Computer: window manager",
                            status=CheckStatus.WARNING,
                            message="No EWMH window manager detected — window_focus may not work",
                            fix_hint=(
                                "Start a window manager before using computer tool:\n"
                                "  mutter --replace --sm-disable &   # lightweight, used in Docker setup\n"
                                "  fluxbox &                          # minimal X11 WM\n"
                                "  i3 &                               # tiling WM"
                            ),
                        )
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass  # xprop unavailable or X server not responding — skip silently

        # Check for pyatspi (Linux accessibility tree support)
        if display:
            if importlib.util.find_spec("pyatspi"):
                results.append(
                    CheckResult(
                        name="Computer: pyatspi",
                        status=CheckStatus.OK,
                        message="AT-SPI2 accessibility tree available (accessibility_tree + click_accessible_element)",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name="Computer: pyatspi",
                        status=CheckStatus.WARNING,
                        message="pyatspi not installed — accessibility_tree and click_accessible_element unavailable",
                        fix_hint=(
                            "pip install pyatspi\n"
                            "(also requires AT-SPI2 system packages: apt install python3-pyatspi)"
                        ),
                    )
                )

    else:
        # Unsupported platform (Windows, FreeBSD, etc.) — no checks available
        results.append(
            CheckResult(
                name="Computer: platform",
                status=CheckStatus.WARNING,
                message=f"Computer tool is only supported on Linux and macOS (current: {sys.platform})",
            )
        )
        return results

    # ffmpeg is required for screen recording (start_recording / record_screen).
    # Only checked on supported platforms (Linux + macOS) since the install hint is platform-specific.
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        results.append(
            CheckResult(
                name="Computer: ffmpeg",
                status=CheckStatus.OK,
                message="screen recording (start_recording / record_screen)",
                details=ffmpeg_path if verbose else None,
            )
        )
    else:
        results.append(
            CheckResult(
                name="Computer: ffmpeg",
                status=CheckStatus.WARNING,
                message="Not found — required for start_recording() and record_screen()",
                fix_hint=(
                    "Linux:  sudo apt install ffmpeg  or  sudo pacman -S ffmpeg\n"
                    "macOS:  brew install ffmpeg"
                ),
            )
        )

    return results


def _check_browser(verbose: bool = False) -> list[CheckResult]:
    """Check Playwright browser installation if browser extra is installed."""
    results: list[CheckResult] = []

    # Only check if playwright is installed
    if not importlib.util.find_spec("playwright"):
        return results

    # Check if browsers are installed by looking at the standard cache directory
    # Playwright stores browsers in PLAYWRIGHT_BROWSERS_PATH or a platform-specific default
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path:
        if sys.platform == "darwin":
            browsers_path = str(Path.home() / "Library" / "Caches" / "ms-playwright")
        else:
            browsers_path = str(Path.home() / ".cache" / "ms-playwright")

    browsers_dir = Path(browsers_path)
    if browsers_dir.exists():
        # Count installed browser directories (e.g., chromium-1148, firefox-1460)
        browser_dirs = [
            d.name
            for d in browsers_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        if browser_dirs:
            results.append(
                CheckResult(
                    name="Browser: Playwright",
                    status=CheckStatus.OK,
                    message=f"{len(browser_dirs)} browser(s) installed",
                    details=", ".join(sorted(browser_dirs)) if verbose else None,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Browser: Playwright",
                    status=CheckStatus.WARNING,
                    message="Playwright installed but no browsers found",
                    fix_hint="playwright install chromium",
                )
            )
    else:
        results.append(
            CheckResult(
                name="Browser: Playwright",
                status=CheckStatus.WARNING,
                message="Playwright installed but no browsers found",
                fix_hint="playwright install chromium",
            )
        )

    return results


def _check_mcp(verbose: bool = False) -> list[CheckResult]:
    """Check MCP server configuration and connectivity."""
    results: list[CheckResult] = []

    config = get_config()

    # Check if MCP is enabled
    if not config.mcp.enabled:
        results.append(
            CheckResult(
                name="MCP: Status",
                status=CheckStatus.SKIPPED,
                message="MCP not enabled",
                fix_hint="Add [mcp] enabled = true to gptme.toml",
            )
        )
        return results

    results.append(
        CheckResult(
            name="MCP: Status",
            status=CheckStatus.OK,
            message=f"Enabled ({len(config.mcp.servers)} server(s) configured)",
        )
    )

    if not config.mcp.servers:
        return results

    # Check each configured server
    for server_config in config.mcp.servers:
        if not server_config.enabled:
            results.append(
                CheckResult(
                    name=f"MCP: {server_config.name}",
                    status=CheckStatus.SKIPPED,
                    message="Disabled in config",
                )
            )
            continue

        if server_config.is_http:
            # HTTP server: check URL reachability
            results.extend(_check_mcp_http_server(server_config, verbose))
        else:
            # stdio server: check command exists
            results.extend(_check_mcp_stdio_server(server_config, verbose))

    return results


def _check_mcp_http_server(
    server_config: MCPServerConfig, verbose: bool = False
) -> list[CheckResult]:
    """Check an HTTP-based MCP server."""
    import urllib.request

    try:
        req = urllib.request.Request(
            server_config.url,
            method="HEAD",
            headers=server_config.headers or {},
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        return [
            CheckResult(
                name=f"MCP: {server_config.name}",
                status=CheckStatus.OK,
                message="HTTP server reachable",
                details=server_config.url if verbose else None,
            )
        ]
    except Exception as e:
        # Accept any HTTP response (even 4xx/5xx) as "reachable"
        error_str = str(e)
        if "HTTP Error" in error_str:
            return [
                CheckResult(
                    name=f"MCP: {server_config.name}",
                    status=CheckStatus.OK,
                    message="HTTP server reachable",
                    details=f"{server_config.url} ({error_str})" if verbose else None,
                )
            ]
        return [
            CheckResult(
                name=f"MCP: {server_config.name}",
                status=CheckStatus.ERROR,
                message=f"Cannot reach server: {e}",
                details=server_config.url if verbose else None,
                fix_hint=f"Check that {server_config.url} is accessible",
            )
        ]


def _check_mcp_stdio_server(
    server_config: MCPServerConfig, verbose: bool = False
) -> list[CheckResult]:
    """Check a stdio-based MCP server."""
    command = server_config.command
    if not command:
        return [
            CheckResult(
                name=f"MCP: {server_config.name}",
                status=CheckStatus.ERROR,
                message="No command configured",
                fix_hint="Set 'command' in MCP server config",
            )
        ]

    # Check if the command binary exists
    cmd_path = shutil.which(command)
    if cmd_path:
        details = None
        if verbose:
            args_str = " ".join(server_config.args) if server_config.args else ""
            details = f"{cmd_path} {args_str}".strip()
        return [
            CheckResult(
                name=f"MCP: {server_config.name}",
                status=CheckStatus.OK,
                message=f"Command '{command}' found",
                details=details,
            )
        ]
    return [
        CheckResult(
            name=f"MCP: {server_config.name}",
            status=CheckStatus.ERROR,
            message=f"Command '{command}' not found",
            fix_hint=f"Install {command} or check PATH",
        )
    ]


def _summarize_results(results: list[CheckResult]) -> dict[str, int]:
    """Count diagnostic results by status."""
    return {
        "total": len(results),
        "ok": sum(1 for result in results if result.status == CheckStatus.OK),
        "warning": sum(1 for result in results if result.status == CheckStatus.WARNING),
        "error": sum(1 for result in results if result.status == CheckStatus.ERROR),
        "skipped": sum(1 for result in results if result.status == CheckStatus.SKIPPED),
    }


def run_diagnostics(verbose: bool = False) -> tuple[list[CheckResult], dict[str, int]]:
    """Run all diagnostic checks.

    Returns:
        Tuple of (results list, summary dict)
    """
    all_results: list[CheckResult] = []

    # Run all checks
    all_results.extend(_check_python_version(verbose))
    all_results.extend(_check_version(verbose))
    all_results.extend(_check_config(verbose))
    all_results.extend(_check_proxy(verbose))
    all_results.extend(_check_api_keys(verbose))
    all_results.extend(_check_default_model(verbose))
    all_results.extend(_check_tools(verbose))
    all_results.extend(_check_python_deps(verbose))
    all_results.extend(_check_computer(verbose))
    all_results.extend(_check_browser(verbose))
    all_results.extend(_check_mcp(verbose))
    all_results.extend(_check_permissions(verbose))

    return all_results, _summarize_results(all_results)


def print_results(
    results: list[CheckResult], summary: dict[str, int], verbose: bool = False
) -> int:
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

            # Show fix hints: always for errors/warnings; also for skipped
            # checks when --verbose (e.g. unconfigured API keys).
            if result.fix_hint and (
                result.status in (CheckStatus.ERROR, CheckStatus.WARNING)
                or (verbose and result.status == CheckStatus.SKIPPED)
            ):
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
@click.option(
    "--fix",
    is_flag=True,
    help="Interactively repair provider authentication and model selection",
)
def main(verbose: bool = False, output_json: bool = False, fix: bool = False):
    """Run system diagnostics for gptme.

    Checks API keys, tools, dependencies, configuration, and permissions
    to identify any issues that might affect gptme operation.

    \b
    Examples:
        gptme-doctor              # Quick health check
        gptme-doctor --verbose    # Detailed output with paths and hints
        gptme-doctor --json       # Machine-readable output
        gptme-doctor --fix        # Repair provider setup in a terminal
    """
    if fix and output_json:
        raise click.UsageError("--fix cannot be used with --json")

    results, summary = run_diagnostics(verbose)
    interactive_repair = fix and _is_interactive_terminal()
    if interactive_repair:
        _validate_oauth_for_repair(results)
        summary = _summarize_results(results)

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
    exit_code = print_results(results, summary, verbose)
    if not fix or not _provider_repair_needed(results):
        sys.exit(exit_code)

    if not interactive_repair:
        click.echo("Interactive repair skipped: run gptme-doctor --fix in a terminal")
        sys.exit(exit_code)

    blocking_source = _model_override_blocking_repair()
    if blocking_source:
        click.echo(
            f"Provider repair cannot replace the active {blocking_source} model "
            "override. Update or unset that override, then rerun gptme-doctor --fix."
        )
        sys.exit(exit_code)

    subscription = _subscription_default_candidate(results)
    try:
        if subscription:
            from ..config import set_config_value
            from ..llm.models import get_recommended_model

            model = f"{subscription}/{get_recommended_model(subscription)}"
            if not click.confirm(
                f"Use the detected {subscription} login as the default ({model})?",
                default=True,
            ):
                sys.exit(exit_code)
            set_config_value("models.default", model)
        else:
            if not click.confirm(
                "No working provider is configured. Run provider setup now?",
                default=True,
            ):
                sys.exit(exit_code)
            from .setup import ask_for_api_key

            provider, _ = ask_for_api_key(require_default_model=True)
            from ..config import set_config_value
            from ..llm.models import get_model

            selected_model = get_model(provider).model
            if not selected_model.startswith(f"{provider}/"):
                selected_model = f"{provider}/{selected_model}"
            set_config_value("models.default", selected_model)
    except (KeyboardInterrupt, click.Abort):
        click.echo("\nProvider repair cancelled.")
        sys.exit(exit_code)
    except Exception as exc:
        click.echo(f"Provider repair failed: {exc}", err=True)
        sys.exit(1)

    repaired_results, repaired_summary = run_diagnostics(verbose)
    repaired_exit_code = print_results(repaired_results, repaired_summary, verbose)
    sys.exit(repaired_exit_code)


if __name__ == "__main__":
    main()
