"""
CLI for gptme utility commands.
"""

import logging
import sys
from pathlib import Path

import click

from ..dirs import get_logs_dir
from ..logmanager import LogManager
from ..message import Message
from ..tools import get_tools, init_tools
from ..tools.chats import list_chats, search_chats


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def main(verbose: bool = False):
    """Utility commands for gptme."""

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@main.group()
def chats():
    """Commands for managing chat logs."""
    # needed since get_prompt() requires tools to be loaded
    if not get_tools():
        init_tools()


@chats.command("ls")
@click.option("-n", "--limit", default=20, help="Maximum number of chats to show.")
@click.option(
    "--summarize", is_flag=True, help="Generate LLM-based summaries for chats"
)
def chats_list(limit: int, summarize: bool):
    """List conversation logs."""
    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init("openai/gpt-4o", interactive=False, tool_allowlist=[])
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
        init("openai/gpt-4o", interactive=False, tool_allowlist=[])
    search_chats(query, max_results=limit)


@chats.command("read")
@click.argument("name")
def chats_read(name: str):
    """Read a specific chat log."""

    logdir = Path(get_logs_dir()) / name
    if not logdir.exists():
        print(f"Chat '{name}' not found")
        return

    log = LogManager.load(logdir)
    for msg in log.log:
        if isinstance(msg, Message):
            print(f"{msg.role}: {msg.content}")


@main.group()
def tokens():
    """Commands for token counting."""
    pass


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
        print("Supported models include: gpt-4, gpt-3.5-turbo, text-davinci-003")
        sys.exit(1)

    # Count tokens
    tokens = enc.encode(text)
    print(f"Token count ({model}): {len(tokens)}")


@main.group()
def context():
    """Commands for context generation."""
    pass


@context.command("generate")
@click.argument("path", type=click.Path(exists=True))
def context_generate(path: str):
    """Index a file or directory for context retrieval."""
    from ..tools.rag import init, rag_index  # fmt: skip

    # Initialize RAG
    init()

    # Index the file/directory
    n_docs = rag_index(path)
    print(f"Indexed {n_docs} documents")


@main.group()
def llm():
    """LLM-related utilities."""
    pass


@llm.command("generate")
@click.argument("prompt", required=False)
@click.option(
    "-m",
    "--model",
    help="Model to use (e.g. openai/gpt-4o, anthropic/claude-3-5-sonnet)",
)
@click.option("--stream/--no-stream", default=False, help="Stream the response")
def llm_generate(prompt: str | None, model: str | None, stream: bool):
    """Generate a response from an LLM without any formatting."""
    import io
    from contextlib import redirect_stderr

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
        from ..llm import _chat_complete, _stream, get_provider_from_model, init_llm  # fmt: skip
        from ..llm.models import get_default_model  # fmt: skip
        from ..message import Message  # fmt: skip
        from ..util import console  # fmt: skip

        # Disable console output
        console.quiet = True

        # Initialize with minimal setup - no tools needed for simple generation
        init(model, interactive=False, tool_allowlist=[])

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
    pass


@tools.command("list")
@click.option(
    "--available/--all", default=True, help="Show only available tools or all tools"
)
@click.option("--langtags", is_flag=True, help="Show language tags for code execution")
def tools_list(available: bool, langtags: bool):
    """List available tools."""
    from ..commands import _gen_help  # fmt: skip
    from ..tools import get_tools, init_tools  # fmt: skip

    # Initialize tools
    init_tools()

    if langtags:
        # Show language tags using existing help generator
        for line in _gen_help(incl_langtags=True):
            if line.startswith("Supported langtags:"):
                print("\nSupported language tags:")
                continue
            if line.startswith("  - "):
                print(line)
        return

    print("Available tools:")
    for tool in get_tools():
        if not available or tool.available:
            status = "✓" if tool.available else "✗"
            print(
                f"""
{status} {tool.name}
   {tool.desc}"""
            )


@tools.command("info")
@click.argument("tool_name")
def tools_info(tool_name: str):
    """Show detailed information about a tool."""
    from ..tools import get_tool, get_tools, init_tools  # fmt: skip

    # Initialize tools
    init_tools()

    tool = get_tool(tool_name)
    if not tool:
        print(f"Tool '{tool_name}' not found. Available tools:")
        for t in get_tools():
            print(f"- {t.name}")
        sys.exit(1)

    print(f"Tool: {tool.name}")
    print(f"Description: {tool.desc}")
    print(f"Available: {'Yes' if tool.available else 'No'}")
    print("\nInstructions:")
    print(tool.instructions)
    if tool.get_examples():
        print("\nExamples:")
        print(tool.get_examples())


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


if __name__ == "__main__":
    main()
