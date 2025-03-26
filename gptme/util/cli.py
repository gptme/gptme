"""
CLI for gptme utility commands.
"""

import logging
import sys

import click

from ..tools import get_tools, init_tools
from ..tools.chats import list_chats, search_chats, read_chat


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
@click.option(
    "--context",
    "-c",
    default=1,
    help="Number of lines of context to show around matches",
)
@click.option(
    "--matches", "-m", default=1, help="Number of matches to show per conversation"
)
def chats_search(query: str, limit: int, summarize: bool, context: int, matches: int):
    """Search conversation logs."""
    if summarize:
        from gptme.init import init  # fmt: skip

        # This isn't the best way to initialize the model for summarization, but it works for now
        init("openai/gpt-4o", interactive=False, tool_allowlist=[])
    search_chats(query, max_results=limit, context_lines=context, max_matches=matches)


@chats.command("read")
@click.argument("name")
@click.option("-n", "--limit", default=5, help="Maximum number of messages to show.")
@click.option("--system", is_flag=True, help="Include system messages")
@click.option(
    "--context",
    "-c",
    default=0,
    help="Number of messages to show before/after each message",
)
@click.option("--start", type=int, help="Start from this message number")
def chats_read(name: str, limit: int, system: bool, context: int, start: int | None):
    """Read a specific chat log."""
    read_chat(
        name,
        max_results=limit,
        incl_system=system,
        context_messages=context,
        start_message=start,
    )


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
