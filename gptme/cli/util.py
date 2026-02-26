"""
CLI for gptme utility commands.
"""

import io
import logging
import sys
from contextlib import redirect_stderr
from pathlib import Path

import click

from ..config import get_config
from ..dirs import get_logs_dir
from ..llm.models import list_models
from ..logmanager import LogManager
from ..mcp.client import MCPClient
from ..message import Message
from ..tools import get_tools, init_tools
from ..tools.chats import list_chats, search_chats
from ..util.context import include_paths


@click.group()
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
            click.echo(f"   API Key: ${provider.name.upper()}_API_KEY (default)")

        if provider.default_model:
            click.echo(f"   Default Model: {provider.default_model}")

        click.echo()


@main.group()
def mcp():
    """Commands for managing MCP servers."""


@mcp.command("list")
def mcp_list():
    """List MCP servers and check their connection health."""

    config = get_config()

    if not config.mcp.enabled:
        click.echo("❌ MCP is disabled in config")
        return

    if not config.mcp.servers:
        click.echo("📭 No MCP servers configured")
        return

    click.echo(f"🔌 Found {len(config.mcp.servers)} MCP server(s):")
    click.echo()

    for server in config.mcp.servers:
        status_icon = "🟢" if server.enabled else "🔴"
        server_type = "HTTP" if server.is_http else "stdio"

        click.echo(f"{status_icon} {server.name} ({server_type})")

        if not server.enabled:
            click.echo("   Status: Disabled")
            click.echo()
            continue

        # Test connection
        try:
            client = MCPClient(config)
            tools, session = client.connect(server.name)
            click.echo(f"   Status: ✅ Connected ({len(tools.tools)} tools available)")

            # Show first few tools
            if tools.tools:
                tool_names = [tool.name for tool in tools.tools[:3]]
                more = (
                    f" (+{len(tools.tools) - 3} more)" if len(tools.tools) > 3 else ""
                )
                click.echo(f"   Tools: {', '.join(tool_names)}{more}")
        except Exception as e:
            click.echo(f"   Status: ❌ Connection failed: {e}")

        click.echo()


@mcp.command("test")
@click.argument("server_name")
def mcp_test(server_name: str):
    """Test connection to a specific MCP server."""

    config = get_config()

    if not config.mcp.enabled:
        click.echo("❌ MCP is disabled in config")
        return

    server = next((s for s in config.mcp.servers if s.name == server_name), None)
    if not server:
        click.echo(f"❌ Server '{server_name}' not found in config")
        return

    if not server.enabled:
        click.echo(f"❌ Server '{server_name}' is disabled")
        return

    server_type = "HTTP" if server.is_http else "stdio"
    click.echo(f"🔌 Testing {server_name} ({server_type})...")

    try:
        client = MCPClient(config)
        tools, session = client.connect(server_name)
        click.echo("✅ Connected successfully!")
        click.echo(f"📋 Available tools ({len(tools.tools)}):")

        for tool in tools.tools:
            click.echo(f"   • {tool.name}: {tool.description or 'No description'}")

    except Exception as e:
        click.echo(f"❌ Connection failed: {e}")


@mcp.command("info")
@click.argument("server_name")
def mcp_info(server_name: str):
    """Show detailed information about an MCP server.

    Checks configured servers first, then searches registries if not found locally.
    """
    from ..mcp.registry import MCPRegistry, format_server_details

    config = get_config()

    # First check if server is configured locally
    server = next((s for s in config.mcp.servers if s.name == server_name), None)

    if server:
        # Show local configuration
        click.echo(f"📋 MCP Server: {server.name}")
        click.echo(f"   Type: {'HTTP' if server.is_http else 'stdio'}")
        click.echo(f"   Enabled: {'✅' if server.enabled else '❌'}")
        click.echo()

        if server.is_http:
            click.echo(f"   URL: {server.url}")
            if server.headers:
                click.echo(f"   Headers: {len(server.headers)} configured")
        else:
            click.echo(f"   Command: {server.command}")
            if server.args:
                click.echo(f"   Args: {' '.join(server.args)}")
            if server.env:
                click.echo(f"   Environment: {len(server.env)} variables")

        # Try to test connection if enabled
        if server.enabled:
            click.echo()
            click.echo("Testing connection...")
            try:
                client = MCPClient(config)
                tools, session = client.connect(server_name)
                click.echo(f"✅ Connected ({len(tools.tools)} tools available)")
            except Exception as e:
                click.echo(f"❌ Connection failed: {e}")
    else:
        # Not found locally, search registries
        click.echo(f"Server '{server_name}' not configured locally.")
        click.echo("🔍 Searching registries...")
        click.echo()

        reg = MCPRegistry()
        try:
            registry_server = reg.get_server_details(server_name)
            if registry_server:
                click.echo(format_server_details(registry_server))
            else:
                click.echo(f"❌ Server '{server_name}' not found in registries either.")
                click.echo("\nTry searching: gptme-util mcp search <query>")
        except Exception as e:
            click.echo(f"❌ Registry search failed: {e}")


@mcp.command("search")
@click.argument("query", required=False, default="")
@click.option(
    "-r",
    "--registry",
    default="all",
    type=click.Choice(["all", "official", "mcp.so"]),
    help="Registry to search",
)
@click.option("-n", "--limit", default=10, help="Maximum number of results")
def mcp_search(query: str, registry: str, limit: int):
    """Search for MCP servers in registries."""
    from ..mcp.registry import MCPRegistry, format_server_list

    if registry == "all":
        click.echo(f"🔍 Searching all registries for '{query}'...")
    else:
        click.echo(f"🔍 Searching {registry} registry for '{query}'...")
    click.echo()

    reg = MCPRegistry()

    try:
        if registry == "all":
            results = reg.search_all(query, limit)
        elif registry == "official":
            results = reg.search_official_registry(query, limit)
        elif registry == "mcp.so":
            results = reg.search_mcp_so(query, limit)
        else:
            click.echo(f"❌ Unknown registry: {registry}")
            return

        if results:
            click.echo(format_server_list(results))
        else:
            click.echo("No servers found.")
    except Exception as e:
        click.echo(f"❌ Search failed: {e}")


@main.group()
def chats():
    """Commands for managing chat logs."""
    # needed since get_prompt() requires tools to be loaded
    if not get_tools():
        init_tools()


@chats.command("list")
@click.option("-n", "--limit", default=20, help="Maximum number of chats to show.")
@click.option(
    "--summarize", is_flag=True, help="Generate LLM-based summaries for chats"
)
def chats_list(limit: int, summarize: bool):
    """List conversation logs."""
    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init(
            "openai/gpt-4o",
            interactive=False,
            tool_allowlist=[],
            tool_format="markdown",
        )
    list_chats(max_results=limit, include_summary=summarize)


@chats.command("search")
@click.argument("query")
@click.option("-n", "--limit", default=20, help="Maximum number of chats to show.")
@click.option(
    "--summarize", is_flag=True, help="Generate LLM-based summaries for chats"
)
def chats_search(query: str, limit: int, summarize: bool):
    """Search conversation logs."""
    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init(
            "openai/gpt-4o",
            interactive=False,
            tool_allowlist=[],
            tool_format="markdown",
        )
    search_chats(query, max_results=limit)


@chats.command("read")
@click.argument("id")
def chats_read(id: str):
    """Read a specific chat log."""

    logdir = get_logs_dir() / id
    if not logdir.exists():
        print(f"Chat '{id}' not found")
        return

    log = LogManager.load(logdir)
    for msg in log.log:
        if isinstance(msg, Message):
            print(f"{msg.role}: {msg.content}")


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
def skills():
    """Browse and inspect skills and lessons."""


@skills.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show both skills and lessons")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def skills_list(show_all: bool, json_output: bool):
    """List available skills (and optionally lessons)."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    if not index.lessons:
        click.echo("No skills or lessons found.")
        return

    # Separate skills and lessons
    skills_items = [item for item in index.lessons if item.metadata.name]
    lessons_items = [item for item in index.lessons if not item.metadata.name]

    if json_output:
        import json

        result: dict = {
            "skills": [
                {
                    "name": s.metadata.name,
                    "description": s.metadata.description or s.description,
                    "path": str(s.path),
                    "category": s.category,
                }
                for s in sorted(skills_items, key=lambda s: s.metadata.name or "")
            ],
        }
        if show_all:
            result["lessons"] = [
                {
                    "title": lesson.title,
                    "category": lesson.category,
                    "keywords": lesson.metadata.keywords[:5],
                    "path": str(lesson.path),
                }
                for lesson in sorted(lessons_items, key=lambda x: x.title)
            ]
        click.echo(json.dumps(result, indent=2))
        return

    # Skills
    if skills_items:
        skills_sorted = sorted(skills_items, key=lambda s: s.metadata.name or "")
        click.echo(f"Skills ({len(skills_sorted)}):\n")
        for skill in skills_sorted:
            name = skill.metadata.name
            desc = skill.metadata.description or skill.description or ""
            if len(desc) > 60:
                desc = desc[:57] + "..."
            click.echo(f"  {name or '':30s} {desc}")
    else:
        click.echo("No skills found.")

    if not show_all:
        if lessons_items:
            click.echo(f"\n({len(lessons_items)} lessons available, use --all to show)")
        return

    # Lessons (grouped by category)
    if lessons_items:
        click.echo(f"\nLessons ({len(lessons_items)}):\n")
        by_category: dict[str, list] = {}
        for lesson in lessons_items:
            by_category.setdefault(lesson.category, []).append(lesson)

        for cat in sorted(by_category.keys()):
            click.echo(f"  [{cat}]")
            for lesson in sorted(by_category[cat], key=lambda x: x.title):
                click.echo(f"    {lesson.title}")
            click.echo()


@skills.command("show")
@click.argument("name")
def skills_show(name: str):
    """Show the full content of a skill or lesson."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    if not index.lessons:
        click.echo("No skills or lessons found.")
        return

    name_lower = name.lower()

    # Search by skill name first, then lesson title/filename
    for item in index.lessons:
        if item.metadata.name and name_lower in item.metadata.name.lower():
            click.echo(f"# {item.metadata.name}")
            if item.metadata.description:
                click.echo(f"\n{item.metadata.description}")
            click.echo(f"\nPath: {item.path}\n")
            click.echo(item.body)
            return

    for item in index.lessons:
        if name_lower in item.title.lower() or name_lower in item.path.stem.lower():
            click.echo(f"# {item.title}")
            click.echo(f"\nPath: {item.path}\n")
            click.echo(item.body)
            return

    click.echo(f"Skill or lesson not found: {name}")
    sys.exit(1)


@skills.command("search")
@click.argument("query")
@click.option("-n", "--limit", default=10, help="Maximum number of results")
def skills_search(query: str, limit: int):
    """Search skills and lessons by keyword."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    if not index.lessons:
        click.echo("No skills or lessons found.")
        return

    results = index.search(query)

    if not results:
        click.echo(f"No results for '{query}'")
        return

    results = results[:limit]
    click.echo(f"Results for '{query}' ({len(results)}):\n")

    for item in results:
        if item.metadata.name:
            label = f"[skill] {item.metadata.name}"
        else:
            label = f"[{item.category}] {item.title}"
        desc = item.metadata.description or item.description or ""
        if len(desc) > 50:
            desc = desc[:47] + "..."
        click.echo(f"  {label:40s} {desc}")


@skills.command("dirs")
def skills_dirs():
    """Show directories searched for skills and lessons."""
    from ..lessons.index import LessonIndex

    index = LessonIndex()

    click.echo("Skill/lesson directories:\n")
    for d in index.lesson_dirs:
        exists = d.exists()
        count = 0
        if exists:
            count = len(list(d.rglob("*.md"))) + len(list(d.rglob("*.mdc")))
        status = f"{count} files" if exists else "not found"
        icon = "+" if exists else "-"
        click.echo(f"  {icon} {d}  ({status})")


@main.group()
def tools():
    """Tool-related utilities."""


@tools.command("list")
@click.option(
    "--available/--all", default=True, help="Show only available tools or all tools"
)
@click.option("--langtags", is_flag=True, help="Show language tags for code execution")
@click.option("--compact", is_flag=True, help="Compact single-line format")
def tools_list(available: bool, langtags: bool, compact: bool):
    """List available tools.

    By default shows only available tools (dependencies installed).
    Use --all to include unavailable tools as well.
    """
    from ..tools import get_available_tools, init_tools  # fmt: skip
    from ..util.tool_format import format_langtags, format_tools_list  # fmt: skip

    # Initialize tools
    init_tools()

    # get_available_tools() returns all discovered tools (loaded or not)
    tools = get_available_tools()

    if langtags:
        print(format_langtags(tools))
        return

    print(format_tools_list(tools, show_all=not available, compact=compact))


@tools.command("info")
@click.argument("tool_name")
@click.option("-v", "--verbose", is_flag=True, help="Show full output (not truncated)")
@click.option("--no-examples", is_flag=True, help="Hide examples section")
@click.option("--no-tokens", is_flag=True, help="Hide token estimates")
def tools_info(tool_name: str, verbose: bool, no_examples: bool, no_tokens: bool):
    """Show detailed information about a tool.

    Displays tool instructions, examples, and token usage estimates.
    Use this to understand how a tool works and how to use it.

    Output is truncated by default. Use -v for full output.
    """
    from ..tools import get_available_tools, get_tool, init_tools  # fmt: skip
    from ..util.tool_format import format_tool_info  # fmt: skip

    # Initialize tools
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
            key, value = arg_str.split("=", 1)
            kwargs[key] = value
        return_val = function[0](**kwargs)
        print(return_val)


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
def models_list(
    provider: str | None,
    pricing: bool,
    vision: bool,
    reasoning: bool,
    simple: bool,
    include_deprecated: bool,
):
    """List available models."""

    list_models(
        provider_filter=provider,
        show_pricing=pricing,
        vision_only=vision,
        reasoning_only=reasoning,
        include_deprecated=include_deprecated,
        simple_format=simple,
        dynamic_fetch=True,
    )


@models.command("info")
@click.argument("model_name")
def models_info(model_name: str):
    """Show detailed information about a specific model."""
    from ..llm.models import get_model  # fmt: skip

    try:
        model = get_model(model_name)
    except Exception as e:
        print(f"Error getting model info: {e}")
        sys.exit(1)

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
