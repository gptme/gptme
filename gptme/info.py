"""
System and environment information utilities.

Provides functions to inspect gptme's installation state, configuration,
and runtime environment. Used by both `--version` and `gptme-doctor`.
"""

import importlib.metadata
import importlib.util
import json
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .dirs import get_logs_dir


@dataclass
class ExtraInfo:
    """Information about an optional dependency/extra."""

    name: str
    installed: bool
    description: str
    packages: list[str] = field(default_factory=list)


@dataclass
class InstallInfo:
    """Information about how gptme was installed."""

    method: str  # pip, pipx, uv, poetry, unknown
    editable: bool
    path: str | None = None


# Define extras with their indicator packages and descriptions
# Note: Only user-facing extras are listed here. Build/dev extras like
# pyinstaller, eval are omitted as they're not useful to show in --version.
# See pyproject.toml [tool.poetry.extras] for the full list.
EXTRAS = [
    ExtraInfo(
        "browser",
        False,
        "Web browsing with Playwright",
        ["playwright"],
    ),
    ExtraInfo(
        "server",
        False,
        "REST API server",
        ["flask"],
    ),
    ExtraInfo(
        "datascience",
        False,
        "Data science tools (numpy, pandas, matplotlib)",
        ["pandas", "matplotlib"],
    ),
    ExtraInfo(
        "tts",
        False,
        "Text-to-speech with Kokoro",
        ["sounddevice"],
    ),
    ExtraInfo(
        "dspy",
        False,
        "Embeddings for RAG/lessons (sentence-transformers)",
        ["sentence_transformers"],
    ),
    ExtraInfo(
        "telemetry",
        False,
        "OpenTelemetry instrumentation",
        ["opentelemetry"],
    ),
    ExtraInfo(
        "youtube",
        False,
        "YouTube transcript extraction",
        ["youtube_transcript_api"],
    ),
    ExtraInfo(
        "acp",
        False,
        "Agent Communication Protocol support",
        ["agent_client_protocol"],
    ),
]


def _is_package_installed(name: str) -> bool:
    """Check if a package is installed."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def get_install_info() -> InstallInfo:
    """Detect how gptme was installed."""
    try:
        dist = importlib.metadata.distribution("gptme")

        # Check installer
        try:
            installer = (dist.read_text("INSTALLER") or "").strip().lower()
        except Exception:
            installer = "unknown"

        # Check if editable via direct_url.json
        editable = False
        path = None
        try:
            direct_url_text = dist.read_text("direct_url.json")
            if direct_url_text:
                data = json.loads(direct_url_text)
                editable = data.get("dir_info", {}).get("editable", False)
                url = data.get("url", "")
                if url.startswith("file://"):
                    path = url[7:]  # Strip file://
        except Exception:
            pass

        # Also check if PathDistribution (another indicator of editable)
        if not editable and type(dist).__name__ == "PathDistribution":
            editable = True

        # Determine method
        if installer == "uv":
            method = "uv"
        elif installer == "poetry":
            method = "poetry"
        elif installer == "pip":
            # Check if installed via pipx (lives in ~/.local/pipx/)
            if path and "pipx" in path:
                method = "pipx"
            else:
                method = "pip"
        else:
            method = installer or "unknown"

        return InstallInfo(method=method, editable=editable, path=path)

    except importlib.metadata.PackageNotFoundError:
        return InstallInfo(method="unknown", editable=False)


def get_installed_extras() -> list[ExtraInfo]:
    """Get list of extras with their installation status."""
    result = []
    for extra in EXTRAS:
        # Check if any of the indicator packages are installed
        installed = any(_is_package_installed(pkg) for pkg in extra.packages)
        result.append(
            ExtraInfo(
                name=extra.name,
                installed=installed,
                description=extra.description,
                packages=extra.packages,
            )
        )
    return result


def get_available_providers() -> list[str]:
    """Get list of configured/available LLM providers."""
    try:
        from .llm import list_available_providers

        providers = list_available_providers()
        return [name for name, _ in providers]
    except Exception:
        return []


def get_default_model() -> str | None:
    """Get the default model from config."""
    try:
        from .config import get_config

        config = get_config()
        return config.get_env("MODEL") or None
    except Exception:
        return None


def get_tool_count() -> int:
    """Get count of available tools."""
    try:
        from .tools import get_available_tools

        tools = get_available_tools(include_mcp=False)
        return sum(1 for t in tools if t.is_available)
    except Exception:
        return 0


def get_quick_health() -> tuple[int, int, int]:
    """Get quick health check counts (ok, warnings, errors).

    This is a fast check that doesn't validate API keys.
    """
    ok = 0
    warnings = 0
    errors = 0

    # Check basic tools
    required_tools = ["python3", "git"]
    for tool in required_tools:
        if shutil.which(tool):
            ok += 1
        else:
            errors += 1

    # Check optional tools
    optional_tools = ["gh", "tmux"]
    for tool in optional_tools:
        if shutil.which(tool):
            ok += 1
        else:
            warnings += 1

    # Check providers configured
    providers = get_available_providers()
    if providers:
        ok += 1
    else:
        warnings += 1

    # Check config exists
    config_info = get_config_info()
    if config_info["config_exists"]:
        ok += 1
    else:
        warnings += 1

    return ok, warnings, errors


def get_system_info() -> dict:
    """Get system information."""
    return {
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "platform_version": platform.release(),
        "machine": platform.machine(),
    }


def get_config_info() -> dict:
    """Get configuration information."""
    from .config import config_path, get_project_config

    info = {
        "logs_dir": str(get_logs_dir()),
        "config_path": config_path,
        "config_exists": Path(config_path).exists(),
    }

    # Check for project config
    cwd = Path.cwd()
    project_cfg = get_project_config(cwd, quiet=True)
    if project_cfg:
        # Try common locations
        for cfg_path in [cwd / "gptme.toml", cwd / ".github" / "gptme.toml"]:
            if cfg_path.exists():
                info["project_config"] = str(cfg_path)
                break

    return info


def format_version_info(verbose: bool = False, output_json: bool = False) -> str:
    """Format version information for display.

    Args:
        verbose: Include additional details like provider list and config paths
        output_json: Return JSON format instead of human-readable
    """
    # Gather all info
    sys_info = get_system_info()
    config_info = get_config_info()
    install_info = get_install_info()
    extras = get_installed_extras()
    providers = get_available_providers()
    default_model = get_default_model()
    tool_count = get_tool_count()
    ok, warnings, errors = get_quick_health()

    installed_extras = [e.name for e in extras if e.installed]
    not_installed_extras = [e.name for e in extras if not e.installed]

    if output_json:
        import json as json_mod

        data = {
            "version": __version__,
            "python": sys_info["python_version"],
            "platform": sys_info["platform"],
            "install": {
                "method": install_info.method,
                "editable": install_info.editable,
                "path": install_info.path,
            },
            "extras": {
                "installed": installed_extras,
                "not_installed": not_installed_extras,
            },
            "providers": providers,
            "default_model": default_model,
            "tools": tool_count,
            "health": {"ok": ok, "warnings": warnings, "errors": errors},
            "paths": {
                "logs": str(get_logs_dir()),
                "config": config_info["config_path"],
                "project_config": config_info.get("project_config"),
            },
        }
        return json_mod.dumps(data, indent=2)

    # Human-readable format
    lines = [f"gptme v{__version__}"]

    # System info with install method
    install_str = install_info.method
    if install_info.editable:
        install_str += " (editable)"
    lines.append(
        f"Python {sys_info['python_version']} on {sys_info['platform']} [{install_str}]"
    )

    # Default model if set
    if default_model:
        lines.append(f"Model: {default_model}")

    # Logs directory
    lines.append(f"Logs: {get_logs_dir()}")

    # Installed extras
    if installed_extras or verbose:
        lines.append("")
        lines.append("Extras:")
        lines.extend(f"  ✓ {extra}" for extra in sorted(installed_extras))
        if verbose:
            lines.extend(
                f"  ✗ {e.name} ({e.description})"
                for e in sorted(
                    [e for e in extras if not e.installed], key=lambda e: e.name
                )
            )

    # Providers
    if providers:
        lines.append("")
        if verbose or len(providers) <= 3:
            lines.append(f"Providers: {', '.join(providers)}")
        else:
            lines.append(f"Providers: {len(providers)} configured")

    # Tools count
    if tool_count:
        lines.append(f"Tools: {tool_count} available")

    # Quick health summary
    lines.append("")
    if errors > 0:
        lines.append(f"Health: ❌ {errors} errors (run `gptme-doctor` for details)")
    elif warnings > 0:
        lines.append(f"Health: ⚠️  {warnings} warnings (run `gptme-doctor` for details)")
    else:
        lines.append("Health: ✅ All good")

    # Config paths in verbose mode
    if verbose:
        lines.append("")
        lines.append(f"Config: {config_info['config_path']}")
        if "project_config" in config_info:
            lines.append(f"Project: {config_info['project_config']}")
        if install_info.path:
            lines.append(f"Source: {install_info.path}")

    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    print(format_version_info(verbose=True))
