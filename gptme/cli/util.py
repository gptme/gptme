"""
CLI for gptme utility commands.

Command groups are split into separate modules for maintainability:
- cmd_agents.py: Live agent scanning (scan for gptme/claude/codex/… processes)
- cmd_chats.py: Chat/conversation management (list, search, export, clean, stats)
- cmd_computer.py: Computer-use tooling (audit-log extracts actions from trajectories)
- cmd_hooks.py: Claude Code hook installation and execution
- cmd_mcp.py: MCP server management (list, test, info, search)
- cmd_batch.py: Batch runner for stdin prompts as fresh non-interactive sessions
- cmd_skills.py: Skills and lessons (list, show, search, install, validate, etc.)
- cmd_snapshot.py: Workspace snapshot management (list snapshots outside a session)

Inline command groups (smaller, live in this file):
- context: RAG index/retrieve plus workspace/git/journal context generation
"""

# Filter requests' overly-strict version-compatibility warning before any
# import path can pull in `requests`. Newer urllib3/chardet/charset_normalizer
# work fine with requests; the warning just pollutes every CLI invocation.
import warnings

warnings.filterwarnings(
    "ignore",
    message=r".*urllib3.*chardet.*charset_normalizer.*",
)

import glob
import importlib
import io
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ..util.git_cmd import GIT_CMD

if TYPE_CHECKING:
    from rich.tree import Tree as RichTree

_LAZY_COMMANDS: dict[str, tuple[str, str]] = {
    "agents": (".cmd_agents", "agents"),
    "attest": (".cmd_attest", "attest"),
    "batch": (".cmd_batch", "batch_cmd"),
    "chats": (".cmd_chats", "chats"),
    "computer": (".cmd_computer", "computer"),
    "hooks": (".cmd_hooks", "hooks"),
    "mcp": (".cmd_mcp", "mcp"),
    "resume": (".cmd_resume", "resume"),
    "skills": (".cmd_skills", "skills"),
    "slop": (".cmd_slop", "slop"),
    "snapshot": (".cmd_snapshot", "snapshot"),
    "status": (".cmd_status", "status"),
}

# Inline groups defined via @main.group() in this file
_INLINE_COMMANDS = frozenset(
    {"providers", "tokens", "context", "llm", "tools", "prompts", "models", "profile"}
)

# All top-level subcommand names for gptme-util — exported for the gptme main CLI
# to enable dynamic dispatch without importing the full util module at startup.
UTIL_SUBCOMMANDS: list[str] = sorted(set(_LAZY_COMMANDS) | _INLINE_COMMANDS)


def get_model_list(*args, **kwargs):
    """Lazy proxy so tests can still patch ``gptme.cli.util.get_model_list``."""
    from ..llm.models import get_model_list as _get_model_list  # fmt: skip

    return _get_model_list(*args, **kwargs)


def list_models(*args, **kwargs):
    """Lazy proxy so util commands don't import model code at module import time."""
    from ..llm.models import list_models as _list_models  # fmt: skip

    return _list_models(*args, **kwargs)


def model_to_dict(model):
    """Lazy proxy used by JSON model output."""
    from ..llm.models import model_to_dict as _model_to_dict  # fmt: skip

    return _model_to_dict(model)


def get_config(*args, **kwargs):
    """Lazy proxy so tests can still patch ``gptme.cli.util.get_config``."""
    from ..config import get_config as _get_config  # fmt: skip

    return _get_config(*args, **kwargs)


class LazyGroup(click.Group):
    """Click group that imports heavyweight subcommands on demand."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        commands = set(super().list_commands(ctx))
        commands.update(_LAZY_COMMANDS)
        return sorted(commands)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command

        target = _LAZY_COMMANDS.get(cmd_name)
        if target is None:
            return None

        module_name, attr_name = target
        module = importlib.import_module(module_name, package=__package__)
        command = getattr(module, attr_name)
        self.add_command(command, cmd_name)
        return command


@click.group(cls=LazyGroup)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def main(verbose: bool = False):
    """Utility commands for gptme."""

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@main.group()
def providers():
    """Commands for managing custom providers."""


@providers.command("list")
def providers_list():
    """List configured custom OpenAI-compatible providers."""
    config = get_config()

    if not config.user.providers:
        click.echo("📭 No custom providers configured")
        click.echo()
        click.echo("To add a custom provider, add to your gptme.toml:")
        click.echo()
        click.echo("[[providers]]")
        click.echo('name = "my-provider"')
        click.echo('base_url = "http://localhost:8000/v1"')
        click.echo('api_key_env = "MY_PROVIDER_API_KEY"')
        click.echo('default_model = "my-model"')
        return

    click.echo(f"🔌 Found {len(config.user.providers)} custom provider(s):")
    click.echo()

    for provider in config.user.providers:
        click.echo(f"📡 {provider.name}")
        click.echo(f"   Base URL: {provider.base_url}")

        # Show API key source (but not the actual key)
        if provider.api_key:
            click.echo("   API Key: (configured directly)")
        elif provider.api_key_env:
            click.echo(f"   API Key: ${provider.api_key_env}")
        else:
            click.echo(
                f"   API Key: ${provider.name.upper().replace('-', '_')}_API_KEY (default)"
            )

        if provider.default_model:
            click.echo(f"   Default Model: {provider.default_model}")

        click.echo()


@providers.command("test")
@click.argument("provider_name")
def providers_test(provider_name: str):
    """Test connectivity to a custom provider.

    Connects to the provider's API and lists available models.
    """
    config = get_config()

    # Find the provider config
    provider_cfg = next(
        (p for p in config.user.providers if p.name == provider_name), None
    )
    if not provider_cfg:
        click.echo(f"❌ Provider '{provider_name}' not found in config")
        click.echo()
        if config.user.providers:
            names = [p.name for p in config.user.providers]
            click.echo(f"Available providers: {', '.join(names)}")
        else:
            click.echo("No custom providers configured. Add one to your gptme.toml:")
            click.echo()
            click.echo("[[providers]]")
            click.echo(f'name = "{provider_name}"')
            click.echo('base_url = "http://localhost:8000/v1"')
        sys.exit(1)

    click.echo(f"🔌 Testing provider: {provider_name}")
    click.echo(f"   Base URL: {provider_cfg.base_url}")

    # Resolve API key
    api_key = None
    key_source = ""
    if provider_cfg.api_key:
        api_key = provider_cfg.api_key
        key_source = "(configured directly)"
    elif provider_cfg.api_key_env:
        api_key = os.environ.get(provider_cfg.api_key_env)
        key_source = f"${provider_cfg.api_key_env}"
        if not api_key:
            click.echo(f"   ❌ API key env var {key_source} is not set")
            sys.exit(1)
    else:
        env_var = f"{provider_name.upper().replace('-', '_')}_API_KEY"
        api_key = os.environ.get(env_var)
        key_source = f"${env_var}"
        if not api_key:
            api_key = "default-key"
            key_source = "(default-key fallback)"

    click.echo(f"   API Key: {key_source}")
    click.echo()

    # Try to connect and list models
    try:
        from openai import OpenAI  # fmt: skip

        client = OpenAI(api_key=api_key, base_url=provider_cfg.base_url, timeout=10)

        click.echo("   Connecting...")
        start = time.monotonic()
        models_response = client.models.list()
        elapsed = time.monotonic() - start
        model_list = list(models_response)

        click.echo(f"   ✅ Connected! ({elapsed:.1f}s)")
        click.echo(f"   📋 Available models ({len(model_list)}):")

        for model in model_list[:10]:
            marker = " ⭐" if model.id == provider_cfg.default_model else ""
            click.echo(f"      • {model.id}{marker}")

        if len(model_list) > 10:
            click.echo(f"      ... and {len(model_list) - 10} more")

        if provider_cfg.default_model:
            found = any(m.id == provider_cfg.default_model for m in model_list)
            if found:
                click.echo(
                    f"\n   ✅ Default model '{provider_cfg.default_model}' is available"
                )
            else:
                click.echo(
                    f"\n   ⚠️  Default model '{provider_cfg.default_model}' "
                    "not found in model list"
                )

    except Exception as e:
        click.echo(f"   ❌ Connection failed: {e}")
        sys.exit(1)


@main.group()
def tokens():
    """Commands for token counting."""


@tokens.command("count")
@click.argument("text", required=False)
@click.option("-m", "--model", default="gpt-4", help="Model to use for token counting.")
@click.option(
    "-f",
    "--file",
    type=click.Path(exists=True, dir_okay=False, allow_dash=True),
    help="File to count tokens in. Use '-' to read from stdin.",
)
def tokens_count(text: str | None, model: str, file: str | None):
    """Count tokens in text or file."""
    from ..util.tokens import len_tokens  # fmt: skip

    # Get text from file if specified (or stdin via "-")
    if file:
        if file == "-":
            text = sys.stdin.read()
        else:
            with open(file) as f:
                text = f.read()
    elif text == "-":
        text = sys.stdin.read()

    if not text:
        print(
            "Error: No text provided. Use --file, a text argument, "
            "or '-' to read from stdin."
        )
        sys.exit(1)

    # Count tokens via gptme's shared tokenizer helper. It handles gptme's
    # canonical "provider/model" names (e.g. "openai/gpt-4o" -> o200k_base),
    # and falls back to a cl100k_base / character estimate for models tiktoken
    # doesn't natively recognize, instead of erroring out. Counts are estimates.
    print(f"Token count ({model}): {len_tokens(text, model)}")


@main.group()
def context():
    """Commands for context generation."""


@context.command("index")
@click.argument("path", type=click.Path(exists=True))
def context_index(path: str):
    """Index a file or directory for context retrieval."""
    from ..tools.rag import _has_gptme_rag, init, rag_index  # fmt: skip

    if not _has_gptme_rag():
        print(
            "Error: gptme-rag is not installed. Please install it to use this feature."
        )
        sys.exit(1)

    # Initialize RAG
    init()

    # Index the file/directory
    n_docs = rag_index(path)
    print(f"Indexed {n_docs} documents")


@context.command("retrieve")
@click.argument("query")
@click.option("--full", is_flag=True, help="Show full context of search results")
def context_retrieve(query: str, full: bool):
    """Search indexed documents for relevant context."""
    from ..tools.rag import _has_gptme_rag, init, rag_search  # fmt: skip

    if not _has_gptme_rag():
        print(
            "Error: gptme-rag is not installed. Please install it to use this feature."
        )
        sys.exit(1)

    # Initialize RAG
    init()

    # Search for the query
    results = rag_search(query, return_full=full)
    print(results)


@context.command("search-conversations")
@click.argument("query")
@click.option(
    "--top-k",
    default=3,
    show_default=True,
    type=int,
    help="Number of results to return",
)
def context_search_conversations(query: str, top_k: int):
    """Search indexed conversations for relevant context.

    Returns the most relevant past conversation snippets for the given query.
    Requires conversations to be indexed first with `rag_index_conversations`
    (use `rag_index_conversations()` via the ipython tool, or `gptme context index` on a directory).
    """
    from ..tools.rag import _has_gptme_rag, init, rag_search  # fmt: skip

    if not _has_gptme_rag():
        print(
            "Error: gptme-rag is not installed. Please install it to use this feature."
        )
        sys.exit(1)

    # Initialize RAG
    init()

    # Search for the query
    results = rag_search(query, return_full=True, top_k=top_k)

    if not results.strip():
        print(
            "No relevant conversations found. "
            "Try indexing conversations first with `rag_index_conversations()` "
            "via the ipython tool."
        )
        return

    print(f"Top {top_k} relevant conversations:\n")
    print(results)


def _git_run(cmd: list[str], check: bool = True, timeout: int = 10) -> tuple[str, bool]:
    """Run a git command and return (stdout, success)."""
    try:
        env = os.environ.copy()
        env.update({"PAGER": "cat", "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
        result = subprocess.run(
            [GIT_CMD] + cmd,
            capture_output=True,
            text=True,
            check=check,
            env=env,
            timeout=timeout,
        )
        if result.returncode != 0:
            return (result.stderr or result.stdout).strip(), False
        return result.stdout.strip(), True
    except subprocess.TimeoutExpired:
        return "", False
    except subprocess.CalledProcessError as e:
        return e.stderr.strip(), False


def _codeblock(langtag: str, content: str) -> str:
    return f"```{langtag}\n{content}\n```"


def _read_gitignore(path: str) -> list[str]:
    ignores: list[str] = []
    for fp in [
        os.path.join(path, ".gitignore"),
        os.path.expanduser("~/.config/git/ignore"),
    ]:
        if os.path.exists(fp):
            with open(fp) as f:
                ignores += [
                    line.strip()
                    for line in f
                    if line.strip() and not line.startswith("#")
                ]
    return ignores


def _walk_directory(
    directory: Path,
    tree: "RichTree",
    excludes: list[str],
    max_depth: int | None,
    depth: int = 1,
) -> None:
    from rich.filesize import decimal
    from rich.markup import escape
    from rich.text import Text

    if max_depth is not None and depth > max_depth:
        return
    try:
        for path in sorted(
            Path(directory).iterdir(), key=lambda p: (p.is_file(), p.name.lower())
        ):
            if any(path.match(e) for e in excludes):
                continue
            try:
                if path.is_dir():
                    style = "dim" if path.name.startswith("__") else ""
                    branch = tree.add(
                        f"[bold magenta][link file://{path}]{escape(path.name)}/",
                        style=style,
                        guide_style=style,
                    )
                    _walk_directory(path, branch, excludes, max_depth, depth + 1)
                else:
                    text = Text(path.name, "green")
                    text.highlight_regex(r"\..*$", "bold red")
                    text.stylize(f"link file://{path}")
                    text.append(f" ({decimal(path.stat().st_size)})", "blue")
                    tree.add(text)
            except OSError as e:
                tree.add(f"[red]{path.name} [Error: {e}]")
    except (PermissionError, OSError) as e:
        tree.add(f"[red][Error: {e}]")


@context.command("git")
def context_git():
    """Summarise the current git repo: branch, recent commits, staged/unstaged changes."""
    output, success = _git_run(["rev-parse", "--git-dir"])
    if not success:
        click.echo("Not a git repository", err=True)
        raise SystemExit(1)

    print("## Git\n")

    log_out, ok = _git_run(
        [
            "log",
            "--pretty=format:%h (%ad) %s",
            "--date=format:%Y-%m-%d %H:%M",
            "-n",
            "5",
        ]
    )
    if ok and log_out:
        print("### Recent commits")
        for line in log_out.split("\n"):
            print(f"- {line}")
        print()

    status_out, ok = _git_run(["status", "-vv"])
    if ok and status_out:
        print(_codeblock("", status_out))


@context.command("tree")
@click.option(
    "--path", type=click.Path(exists=True), default=".", help="Workspace root"
)
@click.option("--max-depth", type=int, default=1, help="Tree depth")
def context_tree(path: str, max_depth: int):
    """Print workspace directory tree (respects .gitignore)."""
    from rich.console import Console
    from rich.tree import Tree

    excludes = _read_gitignore(path) + [".git"]
    abs_path = os.path.abspath(path)
    tree = Tree(abs_path, guide_style="bold bright_blue")
    _walk_directory(Path(path), tree, excludes, max_depth)
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    console.print(tree)
    print("## Workspace structure")
    print(_codeblock("tree", buffer.getvalue().rstrip()))


@context.command("files")
@click.option(
    "--config",
    type=click.Path(exists=True),
    default=None,
    help="Path to gptme.toml (default: auto-discover from git root or cwd)",
)
def context_files(config: str | None):
    """Print the contents of all files listed in gptme.toml [prompt] files.

    Replaces ad-hoc context scripts in non-gptme harnesses (autonomous runs,
    project-monitoring) that manually concatenate gptme.toml prompt files.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib

    # Discover gptme.toml from cwd → git root
    toml_path: Path | None
    if config:
        toml_path = Path(config)
    else:
        candidates = [Path.cwd() / "gptme.toml"]
        root, ok = _git_run(["rev-parse", "--show-toplevel"])
        if ok and root:
            candidates.append(Path(root) / "gptme.toml")
        toml_path = next((p for p in candidates if p.exists()), None)

    if not toml_path or not toml_path.exists():
        click.echo("No gptme.toml found. Use --config to specify path.", err=True)
        raise SystemExit(1)

    with open(toml_path, "rb") as f:
        cfg = tomllib.load(f)

    files = cfg.get("prompt", {}).get("files", [])
    if not files:
        click.echo("No [prompt] files configured in gptme.toml.", err=True)
        raise SystemExit(1)

    workspace = toml_path.parent
    for rel in files:
        fpath = workspace / rel
        if not fpath.exists():
            click.echo(f"# FILE: {rel} (not found)\n", err=True)
            continue
        print(f"## FILE: {rel}\n")
        print(fpath.read_text())
        print("---\n")


@context.command("journal")
@click.option("--days", type=int, default=7, help="Days to look back")
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False),
    help="Journal directory",
)
def context_journal(days: int, path: str | None):
    """Print journal entries from the last N days."""
    # Discover journal dir: prefer workspace-relative path first
    candidates: list[str | None] = [path]
    repo_root, ok = _git_run(["rev-parse", "--show-toplevel"])
    if ok and repo_root:
        candidates.append(os.path.join(repo_root, "journal"))
    candidates += [
        os.path.expanduser("~/journal"),
        os.path.expanduser("~/Documents/journal"),
        os.path.expanduser("~/notes"),
    ]

    journal_dir: str | None = None
    for loc in candidates:
        if loc and os.path.isdir(loc):
            journal_dir = loc
            break

    if not journal_dir:
        click.echo("No journal directory found. Use --path to specify one.", err=True)
        raise SystemExit(1)

    today = datetime.now(tz=timezone.utc).astimezone().date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    entries: list[str] = []
    for date in dates:
        # Support both flat (YYYY-MM-DD-topic.md) and subdirectory (YYYY-MM-DD/topic.md) layouts
        flat_files = glob.glob(os.path.join(journal_dir, f"*{date}*.md"))
        subdir_files = glob.glob(os.path.join(journal_dir, date, "*.md"))
        for file in flat_files + subdir_files:
            with open(file) as f:
                entries.append(f"\n# {date} — {os.path.basename(file)}\n{f.read()}")

    if entries:
        print(f"Journal entries from the last {days} days:\n")
        print("\n".join(entries))
    else:
        print(f"No journal entries found for the last {days} days")


@main.group()
def llm():
    """LLM-related utilities."""


@llm.command("generate")
@click.argument("prompt", required=False)
@click.option(
    "-m",
    "--model",
    help="Model to use (e.g. openai/gpt-4o, anthropic/claude-sonnet-4-6)",
)
@click.option("--stream/--no-stream", default=False, help="Stream the response")
@click.option(
    "--system",
    "system_prompt",
    default="You are a helpful assistant.",
    show_default=True,
    help="System message to prepend.",
)
@click.option(
    "--max-tokens",
    type=int,
    default=None,
    help="Maximum number of tokens to generate.",
)
@click.option(
    "--temperature",
    type=float,
    default=None,
    help="Sampling temperature (0.0=deterministic, higher=more creative). Range: 0.0–2.0.",
)
@click.option(
    "--output-format",
    type=click.Choice(["text", "json"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format: 'text' (default) or 'json' (includes model and usage metadata). Incompatible with --stream.",
)
def llm_generate(
    prompt: str | None,
    model: str | None,
    stream: bool,
    system_prompt: str,
    max_tokens: int | None,
    temperature: float | None,
    output_format: str,
):
    """Generate a response from an LLM without any formatting."""

    # Suppress all logging output to get clean response
    logging.getLogger().setLevel(logging.CRITICAL)

    # Get prompt from stdin if not provided as argument
    if not prompt:
        if sys.stdin.isatty():
            print(
                "Error: No prompt provided. Pipe text to stdin or provide as argument.",
                file=sys.stderr,
            )
            sys.exit(1)
        prompt = sys.stdin.read().strip()

    if not prompt:
        print("Error: Empty prompt provided.", file=sys.stderr)
        sys.exit(1)

    # Validate parameters before initialization (before redirect_stderr
    # so Click can print errors to real stderr, not the captured sink)
    if model is not None and not model.strip():
        raise click.UsageError("Model name cannot be empty.")
    if max_tokens is not None and max_tokens <= 0:
        raise click.UsageError("--max-tokens must be a positive integer.")
    if temperature is not None and not (0.0 <= temperature <= 2.0):
        raise click.UsageError("--temperature must be between 0.0 and 2.0.")
    if output_format == "json" and stream:
        raise click.UsageError("--output-format json is incompatible with --stream.")

    # Capture stderr to suppress console output during initialization
    stderr_capture = io.StringIO()

    from ..util import console  # fmt: skip

    # Disable console output during init, and always restore it: the console
    # is a shared module-level object, so leaving quiet=True would silence all
    # later console output in this process (notably in-process CLI tests).
    prev_quiet = console.quiet
    try:
        with redirect_stderr(stderr_capture):
            from ..init import init  # fmt: skip
            from ..llm import (  # fmt: skip
                _chat_complete,
                _stream,
                get_provider_from_model,
                init_llm,
            )
            from ..llm.models import get_default_model  # fmt: skip
            from ..message import Message  # fmt: skip

            console.quiet = True

            # Initialize with minimal setup - no tools needed for simple generation
            try:
                init(
                    model, interactive=False, tool_allowlist=[], tool_format="markdown"
                )
            except ValueError as e:
                raise click.UsageError(str(e)) from e

            # Get model or use default
            if not model:
                default_model = get_default_model()
                if not default_model:
                    raise click.UsageError(
                        "No model specified and no default model available."
                    )
                model = default_model.full

            # Ensure provider is initialized
            try:
                provider = get_provider_from_model(model)
                init_llm(provider)
            except ValueError as e:
                raise click.UsageError(str(e)) from e
    finally:
        console.quiet = prev_quiet

    # Anthropic requires the first message to be a system message
    messages = [Message("system", system_prompt), Message("user", prompt)]

    try:
        if stream:
            # Stream response directly to stdout
            for chunk in _stream(
                messages, model, None, max_tokens=max_tokens, temperature=temperature
            ):
                print(chunk, end="", flush=True)
            print()  # Final newline
        elif output_format == "json":
            # Return structured JSON with content and metadata
            response, metadata = _chat_complete(
                messages, model, None, max_tokens=max_tokens, temperature=temperature
            )
            result: dict = {
                "content": response,
                "model": (metadata.get("model") if metadata else None) or model,
                "usage": dict(metadata["usage"])
                if metadata and "usage" in metadata
                else None,
            }
            print(json.dumps(result))
        else:
            # Get complete response and print it (plain text)
            response, _ = _chat_complete(
                messages, model, None, max_tokens=max_tokens, temperature=temperature
            )
            print(response)
    except Exception as e:
        print(f"Error generating response: {e}", file=sys.stderr)
        sys.exit(1)


@main.group()
def tools():
    """Tool-related utilities."""


@tools.command("list")
@click.option(
    "--available/--all", default=True, help="Show only available tools or all tools"
)
@click.option("--langtags", is_flag=True, help="Show language tags for code execution")
@click.option("--compact", is_flag=True, help="Compact single-line format")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def tools_list(available: bool, langtags: bool, compact: bool, as_json: bool):
    """List available tools.

    By default shows only available tools (dependencies installed).
    Use --all to include unavailable tools as well.
    """
    from ..tools import get_available_tools, init_tools  # fmt: skip
    from ..util.tool_format import (  # fmt: skip
        format_langtags,
        format_tools_list,
        tool_to_dict,
    )

    # Suppress console output during init for JSON mode (e.g. rich console.log)
    if as_json:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            init_tools()
    else:
        init_tools()

    # get_available_tools() returns all discovered tools (loaded or not)
    tools = get_available_tools()

    if as_json:
        tool_list = sorted(tools, key=lambda t: t.name)
        if available:
            tool_list = [t for t in tool_list if t.is_available]
        print(json.dumps([tool_to_dict(t) for t in tool_list], indent=2))
        return

    if langtags:
        print(format_langtags(tools))
        return

    print(format_tools_list(tools, show_all=not available, compact=compact))


@tools.command("info")
@click.argument("tool_name")
@click.option("-v", "--verbose", is_flag=True, help="Show full output (not truncated)")
@click.option("--no-examples", is_flag=True, help="Hide examples section")
@click.option("--no-tokens", is_flag=True, help="Hide token estimates")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def tools_info(
    tool_name: str, verbose: bool, no_examples: bool, no_tokens: bool, as_json: bool
):
    """Show detailed information about a tool.

    Displays tool instructions, examples, and token usage estimates.
    Use this to understand how a tool works and how to use it.

    Output is truncated by default. Use -v for full output.
    """
    from ..tools import get_available_tools, get_tool, init_tools  # fmt: skip
    from ..util.tool_format import format_tool_info, tool_to_dict  # fmt: skip

    # Suppress console output during init for JSON mode (e.g. rich console.log)
    if as_json:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            init_tools()
    else:
        init_tools()

    # Look in both loaded and all available tools
    tool = get_tool(tool_name)
    if not tool:
        available_dict = {t.name: t for t in get_available_tools()}
        if tool_name in available_dict:
            tool = available_dict[tool_name]
        else:
            if as_json:
                click.echo(
                    json.dumps(
                        {
                            "tool": tool_name,
                            "error": f"Tool '{tool_name}' not found",
                            "available_tools": sorted(available_dict.keys()),
                        },
                        indent=2,
                    )
                )
            else:
                print(f"Tool '{tool_name}' not found. Available tools:")
                for name in sorted(available_dict.keys()):
                    print(f"  - {name}")
            sys.exit(1)

    if as_json:
        d = tool_to_dict(tool)
        # Include full instructions and examples for info output
        d["instructions"] = tool.instructions.strip() if tool.instructions else ""
        examples = tool.get_examples()
        d["examples"] = examples.strip() if examples else ""
        print(json.dumps(d, indent=2))
        return

    print(
        format_tool_info(
            tool,
            include_examples=not no_examples,
            include_tokens=not no_tokens,
            truncate=not verbose,
        )
    )


@tools.command("call")
@click.argument("tool_name")
@click.argument("function_name")
@click.option(
    "--arg",
    "-a",
    multiple=True,
    help="Arguments to pass to the function. Format: key=value",
)
def tools_call(tool_name: str, function_name: str, arg: list[str]):
    """Call a tool with the given arguments."""
    from ..tools import get_available_tools, get_tool, init_tools  # fmt: skip

    # Load the requested tool even if it is not part of the default toolchain.
    # Some tools (e.g. computer, rag, subagent) expose callable functions but
    # are not loaded by default, so a bare init_tools() would leave them
    # uncallable. init_tools raises ValueError for two distinct reasons:
    #   (1) tool name is genuinely unknown — safe to fall back to default init
    #       so the not-found branch can enumerate the full tool list.
    #   (2) tool matched get_available_tools() but was inexplicably absent from
    #       loaded_tools after init — internal consistency failure, must not be
    #       swallowed.
    # Pre-flighting against get_available_tools() separates the two cases
    # without needing to parse error message text. Only route to targeted init
    # when the tool is available (has its runtime deps installed); tools that
    # are discovered but unavailable (is_available=False) fall through to the
    # default init so get_toolchain's strict-mode dep check is never hit.
    available_names = {t.name for t in get_available_tools() if t.is_available}
    if tool_name in available_names:
        init_tools(allowlist=[tool_name])
    else:
        init_tools()

    tool = get_tool(tool_name)
    if not tool:
        print(f"Tool '{tool_name}' not found. Available tools:")
        for t in sorted(get_available_tools(), key=lambda t: t.name):
            print(f"- {t.name}")
        sys.exit(1)

    function = (
        [f for f in tool.functions if f.name == function_name] or None
        if tool.functions
        else None
    )
    if not function:
        if not tool.functions:
            # Most core tools (shell, patch, save, read, …) expose their
            # behaviour through a single ``execute`` entrypoint rather than
            # discrete named functions, so they cannot be reached via
            # ``tools call``. Say so explicitly instead of the misleading
            # "No functions available for this tool."
            if tool.execute is not None:
                print(
                    f"Tool '{tool_name}' has no individually-callable functions; "
                    "it runs through a single execute entrypoint and is not "
                    "callable via 'tools call'."
                )
            else:
                print(f"Tool '{tool_name}' exposes no callable functions.")
            sys.exit(1)
        print(f"Function '{function_name}' not found in tool '{tool_name}'.")
        print("Available functions:")
        for f in tool.functions:
            print(f"- {f.name}")
        sys.exit(1)
    else:
        # Parse arguments into a dictionary, ensuring proper typing
        kwargs = {}
        for arg_str in arg:
            if "=" not in arg_str:
                click.echo(
                    f"Error: Argument must be in key=value format, got: {arg_str}",
                    err=True,
                )
                sys.exit(1)
            key, value = arg_str.split("=", 1)
            kwargs[key] = value
        try:
            return_val = function[0].fn(**kwargs)
            print(return_val)
        except TypeError as e:
            click.echo(f"Error calling function: {e}", err=True)
            sys.exit(1)


@main.group()
def prompts():
    """Commands for prompt utilities."""


@prompts.command("expand")
@click.argument("prompt", nargs=-1, required=True)
def prompts_expand(prompt: tuple[str, ...]):
    """Expand a prompt to show what will be sent to the LLM.

    Shows exactly how file paths in prompts are expanded into message content,
    using the same logic as the main gptme tool.
    """

    # Join all prompt arguments
    full_prompt = "\n\n".join(prompt)

    # Use the existing include_paths function to expand the prompt
    from ..message import Message  # fmt: skip
    from ..util.context import include_paths  # fmt: skip

    original_msg = Message("user", full_prompt)
    # This utility is for inspecting path expansion itself, so it must ignore
    # the ambient GPTME_DISABLE_PATH_INCLUDE setting used by automation.
    disabled_path_include = os.environ.pop("GPTME_DISABLE_PATH_INCLUDE", None)
    try:
        expanded_msg = include_paths(original_msg, workspace=Path.cwd())
    finally:
        if disabled_path_include is not None:
            os.environ["GPTME_DISABLE_PATH_INCLUDE"] = disabled_path_include

    # Print the expanded content exactly as it would be sent to the LLM
    print(expanded_msg.content)


@main.group()
def models():
    """Model-related utilities."""


@models.command("list")
@click.option("--provider", help="Filter by provider (e.g., openai, anthropic, gemini)")
@click.option("--pricing", is_flag=True, help="Show pricing information")
@click.option("--vision", is_flag=True, help="Show only models with vision support")
@click.option(
    "--reasoning", is_flag=True, help="Show only models with reasoning support"
)
@click.option(
    "--simple", is_flag=True, help="Output one model per line as provider/model"
)
@click.option(
    "--include-deprecated",
    is_flag=True,
    help="Include deprecated/sunset models in the listing",
)
@click.option(
    "--available",
    is_flag=True,
    help="Only show models from providers with configured API keys",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def models_list(
    provider: str | None,
    pricing: bool,
    vision: bool,
    reasoning: bool,
    simple: bool,
    include_deprecated: bool,
    available: bool,
    as_json: bool,
):
    """List available models."""

    if as_json:
        # Keep JSON output machine-readable even if provider discovery logs warnings.
        # redirect_stdout/redirect_stderr suppresses print() noise; logging.disable
        # suppresses Rich-formatted log output (httpx retry messages etc.) that escapes
        # through Rich's pre-captured file handle and is not affected by sys.stderr redirect.
        logging.disable(logging.INFO)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                from ..llm import list_available_providers  # fmt: skip

                configured = (
                    {
                        configured_provider
                        for configured_provider, _ in list_available_providers()
                    }
                    if available
                    else None
                )
                models = get_model_list(
                    provider_filter=provider,
                    vision_only=vision,
                    reasoning_only=reasoning,
                    include_deprecated=include_deprecated,
                    dynamic_fetch=True,
                )
        finally:
            logging.disable(logging.NOTSET)
        if configured is not None:
            models = [model for model in models if model.provider_key in configured]
        click.echo(json.dumps([model_to_dict(model) for model in models], indent=2))
        return

    list_models(
        provider_filter=provider,
        show_pricing=pricing,
        vision_only=vision,
        reasoning_only=reasoning,
        include_deprecated=include_deprecated,
        simple_format=simple,
        dynamic_fetch=True,
        available_only=available,
        json_output=as_json,
    )


@models.command("info")
@click.argument("model_name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def models_info(model_name: str, as_json: bool):
    """Show detailed information about a specific model."""
    from ..llm.models import get_model  # fmt: skip

    try:
        model = get_model(model_name)
    except Exception as e:
        print(f"Error getting model info: {e}")
        sys.exit(1)

    # Warn (on stderr, so it never corrupts stdout/JSON) when the provider
    # prefix isn't recognized — get_model() returns generic fallback metadata
    # for unknown providers, so a typo like 'anthropc/...' silently shows fake
    # values. Mirror the provider check that `models test` performs. Only
    # applies to fully-qualified 'provider/model' names; bare names and known
    # custom providers (e.g. lmstudio/...) don't trigger the warning.
    if "/" in model_name:
        from ..llm import get_provider_from_model  # fmt: skip

        try:
            get_provider_from_model(model_name)
        except ValueError:
            click.echo(
                f"⚠️  Unrecognized provider in '{model_name}'; showing generic "
                "fallback metadata. Run 'gptme-util models list --available' "
                "to see known models.",
                err=True,
            )

    if as_json:
        print(json.dumps(model_to_dict(model), indent=2))
        return

    print(f"Model: {model.full}")
    print(f"Provider: {model.provider}")
    print(f"Context window: {model.context:,} tokens")
    if model.max_output:
        print(f"Max output: {model.max_output:,} tokens")

    print(f"Streaming: {'Yes' if model.supports_streaming else 'No'}")
    print(f"Vision: {'Yes' if model.supports_vision else 'No'}")
    print(f"Reasoning: {'Yes' if model.supports_reasoning else 'No'}")

    if model.price_input or model.price_output:
        print(
            f"Pricing: ${model.price_input:.2f} input / ${model.price_output:.2f} output per 1M tokens"
        )

    if model.knowledge_cutoff:
        print(f"Knowledge cutoff: {model.knowledge_cutoff.strftime('%Y-%m-%d')}")

    if model.deprecated:
        print("Status: DEPRECATED")


@models.command("test")
@click.argument("model_name", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def models_test(model_name: str | None, as_json: bool):
    """Test connectivity to a model by making a minimal API call.

    Verifies that the API key is configured, the model is reachable, and
    returns a response. Useful for troubleshooting provider setup and verifying
    model availability.

    Examples:
        gptme-util models test                    # test default model from config
        gptme-util models test anthropic          # test provider default
        gptme-util models test anthropic/claude-opus-4-7  # specific model
        gptme-util models test --json anthropic   # machine-readable output
    """
    from ..llm import (
        PROVIDER_API_KEYS,
        get_provider_from_model,
        init_llm,
    )
    from ..llm import (
        PROVIDER_DEFAULT_MODELS as _PROVIDER_DEFAULT_MODELS,
    )
    from ..message import Message

    # Resolve model name
    if model_name is None:
        config = get_config()
        model_name = config.get_env("MODEL") or "anthropic/claude-haiku-4-5"
        if not as_json:
            click.echo(f"No model specified, using: {model_name}")

    # Resolve bare provider name to a default model
    if "/" not in model_name and model_name in PROVIDER_API_KEYS:
        provider = model_name
        if provider not in _PROVIDER_DEFAULT_MODELS:
            err = f"No default model for '{provider}': specify a full model name (e.g. azure/my-deployment)"
            if as_json:
                click.echo(
                    json.dumps(
                        {"model": model_name, "success": False, "error": err}, indent=2
                    )
                )
            else:
                click.echo(f"❌ {err}")
            sys.exit(1)
        model_name = _PROVIDER_DEFAULT_MODELS[provider]
        if not as_json:
            click.echo(f"Using default model for {provider}: {model_name}")

    result: dict = {"model": model_name, "success": False}

    # Check if API key is configured
    try:
        provider = get_provider_from_model(model_name)
    except Exception as e:
        result["error"] = f"Unknown model or provider: {e}"
        if as_json:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"❌ {result['error']}")
            click.echo()
            click.echo("Try: gptme-util models list --available")
        sys.exit(1)

    result["provider"] = str(provider)

    # Check API key availability
    env_var = PROVIDER_API_KEYS.get(str(provider))
    if env_var:
        config = get_config()
        if not config.get_env(env_var):
            result["error"] = f"API key not configured: ${env_var} is not set"
            if as_json:
                click.echo(json.dumps(result, indent=2))
            else:
                click.echo(f"❌ {result['error']}")
                click.echo()
                click.echo(f"Set it with: export {env_var}=your-api-key")
                click.echo("Or add to ~/.config/gptme/config.toml under [env]")
            sys.exit(1)
        result["api_key_env"] = env_var

    if not as_json:
        click.echo(f"🔌 Testing model: {model_name}")
        if env_var:
            click.echo(f"   API key: ${env_var} ✓")
        click.echo("   Sending test message...")

    # Make a minimal API call
    try:
        init_llm(provider)
        from ..llm import _chat_complete  # fmt: skip

        start = time.monotonic()
        response, metadata = _chat_complete(
            [
                Message("system", "You are a test assistant."),
                Message("user", "Reply with exactly: OK"),
            ],
            model_name,
            tools=None,
            max_tokens=5,
        )
        elapsed = time.monotonic() - start

        result["success"] = True
        result["response"] = response.strip()
        result["latency_ms"] = round(elapsed * 1000)
        if metadata:
            result["metadata"] = {k: v for k, v in metadata.items() if v is not None}

        if as_json:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"   ✅ Response: {response.strip()!r} ({elapsed:.1f}s)")
            click.echo()
            click.echo(f"✅ Model {model_name!r} is working correctly")

    except Exception as e:
        result["error"] = str(e)
        if as_json:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"   ❌ Request failed: {e}")
            click.echo()
            click.echo("Common causes:")
            click.echo("  • Invalid or expired API key")
            click.echo("  • Model name not recognized by provider")
            click.echo("  • Rate limit exceeded")
            click.echo("  • Network connectivity issue")
        sys.exit(1)


@main.group("profile")
def profile_group():
    """Commands for managing agent profiles.

    Profiles define system prompts, tool access, and behavior rules.
    Tool restrictions are hard-enforced in subagent and CLI mode.

    Example:
        gptme-util profile list          # List all profiles
        gptme-util profile show explorer  # Show profile details
    """


@profile_group.command("list")
def profile_list():
    """List available agent profiles."""
    from rich.console import Console
    from rich.table import Table

    from ..profiles import list_profiles as list_available_profiles

    console = Console()
    profiles = list_available_profiles()

    table = Table(title="Available Agent Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="green")
    table.add_column("Tools", style="yellow")
    table.add_column("Behavior", style="magenta")

    for name, prof in sorted(profiles.items()):
        tools_str = ", ".join(prof.tools) if prof.tools is not None else "all"
        behavior_flags = []
        if prof.behavior.read_only:
            behavior_flags.append("read-only")
        if prof.behavior.no_network:
            behavior_flags.append("no-network")
        if prof.behavior.confirm_writes:
            behavior_flags.append("confirm-writes")
        behavior_str = ", ".join(behavior_flags) if behavior_flags else "default"

        table.add_row(name, prof.description, tools_str, behavior_str)

    console.print(table)


@profile_group.command("show")
@click.argument("name")
def profile_show(name: str):
    """Show details for a specific profile."""
    from rich.console import Console
    from rich.panel import Panel

    from ..profiles import get_profile

    console = Console()
    prof = get_profile(name)

    if not prof:
        console.print(f"[red]Unknown profile: {name}[/red]")
        console.print("Use 'gptme-util profile list' to see available profiles.")
        sys.exit(1)

    tools_str = ", ".join(prof.tools) if prof.tools is not None else "all tools"

    behavior_flags = []
    if prof.behavior.read_only:
        behavior_flags.append("read-only")
    if prof.behavior.no_network:
        behavior_flags.append("no-network")
    if prof.behavior.confirm_writes:
        behavior_flags.append("confirm-writes")
    behavior_str = ", ".join(behavior_flags) if behavior_flags else "none (default)"

    content = f"""[cyan]Name:[/cyan] {prof.name}
[cyan]Description:[/cyan] {prof.description}
[cyan]Tools:[/cyan] {tools_str}
[cyan]Behavior:[/cyan] {behavior_str}
"""

    if prof.system_prompt:
        content += f"\n[cyan]System Prompt:[/cyan]\n{prof.system_prompt}"

    console.print(Panel(content, title=f"Profile: {name}"))

    console.print(
        "\n[dim]Note: Tool restrictions are hard-enforced in subagent and CLI mode. "
        "Behavior rules (read_only, no_network) remain soft/prompting-based.[/dim]"
    )


@profile_group.command("validate")
def profile_validate():
    """Validate all profiles against available tools.

    Checks that tool names specified in profiles match actual loaded tools.
    """
    from rich.console import Console

    from ..profiles import list_profiles as list_available_profiles
    from ..tools import get_available_tools

    console = Console()
    profiles = list_available_profiles()
    available = {t.name for t in get_available_tools()}

    has_errors = False
    for name, prof in sorted(profiles.items()):
        unknown = prof.validate_tools(available)
        if unknown:
            has_errors = True
            console.print(
                f"[red]Profile '{name}': unknown tools: {', '.join(unknown)}[/red]"
            )
        else:
            tools_desc = (
                f"{len(prof.tools)} tools" if prof.tools is not None else "all tools"
            )
            console.print(f"[green]Profile '{name}': OK ({tools_desc})[/green]")

    if has_errors:
        console.print(f"\n[dim]Available tools: {', '.join(sorted(available))}[/dim]")
        sys.exit(1)
    else:
        console.print("\n[green]All profiles valid.[/green]")


if __name__ == "__main__":
    main()
