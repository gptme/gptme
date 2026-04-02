"""
CLI for gptme utility commands.

Command groups are split into separate modules for maintainability:
- cmd_chats.py: Chat/conversation management (list, search, export, clean, stats)
- cmd_hooks.py: Claude Code hook installation and execution
- cmd_mcp.py: MCP server management (list, test, info, search)
- cmd_skills.py: Skills and lessons (list, show, search, install, validate, etc.)
"""

import io
import json
import logging
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import click

from ..config import get_config
from ..llm.models import get_model_list, list_models, model_to_dict
from ..message import Message
from ..util.context import include_paths
from .cmd_chats import chats
from .cmd_hooks import hooks
from .cmd_mcp import mcp
from .cmd_skills import skills


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def main(verbose: bool = False):
    """Utility commands for gptme."""

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


# Register command groups from submodules
main.add_command(chats)
main.add_command(hooks)
main.add_command(mcp)
main.add_command(skills)


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
    "-f", "--file", type=click.Path(exists=True), help="File to count tokens in."
)
def tokens_count(text: str | None, model: str, file: str | None):
    """Count tokens in text or file."""
    import tiktoken  # fmt: skip

    # Get text from file if specified
    if file:
        with open(file) as f:
            text = f.read()
    elif not text and not sys.stdin.isatty():
        text = sys.stdin.read()

    if not text:
        print("Error: No text provided. Use --file or pipe text to stdin.")
        return

    # Validate model
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        print(f"Error: Model '{model}' not supported by tiktoken.")
        sys.exit(1)

    # Count tokens
    tokens = enc.encode(text)
    print(f"Token count ({model}): {len(tokens)}")


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
def llm_generate(prompt: str | None, model: str | None, stream: bool):
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

    # Capture stderr to suppress console output during initialization
    stderr_capture = io.StringIO()

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
        from ..util import console  # fmt: skip

        # Disable console output
        console.quiet = True

        # Initialize with minimal setup - no tools needed for simple generation
        init(model, interactive=False, tool_allowlist=[], tool_format="markdown")

        # Get model or use default
        if not model:
            default_model = get_default_model()
            if not default_model:
                print(
                    "Error: No model specified and no default model available.",
                    file=sys.stderr,
                )
                sys.exit(1)
            model = default_model.full

        # Ensure provider is initialized
        try:
            provider = get_provider_from_model(model)
            init_llm(provider)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Create message
    messages = [Message("user", prompt)]

    try:
        if stream:
            # Stream response directly to stdout
            for chunk in _stream(messages, model, None):
                print(chunk, end="", flush=True)
            print()  # Final newline
        else:
            # Get complete response and print it
            response = _chat_complete(messages, model, None)
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
    from ..tools import get_tool, get_tools, init_tools  # fmt: skip

    # Initialize tools
    init_tools()

    tool = get_tool(tool_name)
    if not tool:
        print(f"Tool '{tool_name}' not found. Available tools:")
        for t in get_tools():
            print(f"- {t.name}")
        sys.exit(1)

    function = (
        [f for f in tool.functions if f.__name__ == function_name] or None
        if tool.functions
        else None
    )
    if not function:
        print(f"Function '{function_name}' not found in tool '{tool_name}'.")
        if tool.functions:
            print("Available functions:")
            for f in tool.functions:
                print(f"- {f.__name__}")
        else:
            print("No functions available for this tool.")
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
            return_val = function[0](**kwargs)
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
    original_msg = Message("user", full_prompt)
    expanded_msg = include_paths(original_msg, workspace=Path.cwd())

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
