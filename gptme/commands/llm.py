"""
LLM-related commands: model, tools, context, tokens.
"""

from collections import defaultdict

from .base import CommandContext, command


def _complete_model(partial: str, _prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete model names using dynamic fetching with caching.

    Uses the same model listing logic as gptme-util models list --simple.
    Caching is handled by get_model_list() in models.py.
    """
    from ..llm.models import (  # fmt: skip
        MODELS,
        PROVIDERS,
        get_default_model,
        get_model_list,
    )

    completions: list[tuple[str, str]] = []
    current = get_default_model()

    # Check if user is typing a provider prefix
    if "/" not in partial:
        # Show provider/ prefixes that match
        for provider in PROVIDERS:
            provider_prefix = f"{provider}/"
            if provider_prefix.startswith(partial) or provider.startswith(partial):
                # Count models for this provider
                model_count = len(MODELS.get(provider, {}))
                desc = f"{model_count} models" if model_count else "dynamic"
                completions.append((provider_prefix, desc))

    # Get full model list (cached in get_model_list)
    try:
        models = get_model_list(dynamic_fetch=True)
        for model_meta in models:
            full_name = model_meta.full
            if full_name.startswith(partial):
                is_current = current and current.full == full_name
                desc = "(current)" if is_current else ""
                completions.append((full_name, desc))
    except Exception:
        # Fall back to empty list on error (provider prefixes will still show)
        pass

    # Deduplicate while preserving order
    seen = set()
    unique_completions = []
    for item in completions:
        if item[0] not in seen:
            seen.add(item[0])
            unique_completions.append(item)

    return unique_completions[:30]  # Limit to 30 completions


@command("model", aliases=["models"], completer=_complete_model)
def cmd_model(ctx: CommandContext) -> None:
    """List or switch models."""
    from ..config import ChatConfig  # fmt: skip
    from ..llm.models import (  # fmt: skip
        get_default_model,
        set_default_model,
    )

    ctx.manager.undo(1, quiet=True)
    if ctx.args:
        new_model = ctx.args[0]
        set_default_model(new_model)
        # Persist the model change to config so it survives restart/resume
        chat_config = ChatConfig.from_logdir(ctx.manager.logdir)
        chat_config.model = new_model
        chat_config.save()
        print(f"Set model to {new_model}")
    else:
        model = get_default_model()
        assert model
        print(f"Current model: {model.full}")
        print(
            f"  price: input ${model.price_input}/Mtok, output ${model.price_output}/Mtok"
        )
        print(f"  context: {model.context}, max output: {model.max_output}")
        print(
            f"  (streaming: {model.supports_streaming}, vision: {model.supports_vision})"
        )

        _print_available_models()


@command("tools")
def cmd_tools(ctx: CommandContext) -> None:
    """Show available tools."""
    from ..message import len_tokens  # fmt: skip
    from ..tools import get_tool_format, get_tools  # fmt: skip

    ctx.manager.undo(1, quiet=True)
    print("Available tools:")
    for tool in get_tools():
        print(
            f"""
  # {tool.name}
    {tool.desc.rstrip(".")}
    tokens (example): {len_tokens(tool.get_examples(get_tool_format()), "gpt-4")}"""
        )


@command("context")
def cmd_context(ctx: CommandContext) -> None:
    """Show context token usage breakdown."""
    from ..llm.models import get_default_model  # fmt: skip
    from ..tools import ToolUse  # fmt: skip
    from ..util import console  # fmt: skip
    from ..util.tokens import len_tokens  # fmt: skip

    ctx.manager.undo(1, quiet=True)

    # Try to use the current model's tokenizer, fallback to gpt-4
    current_model = get_default_model()
    tokenizer_model = "gpt-4"
    is_approximate = True

    if current_model:
        # Use matching tokenizer for OpenAI models
        if current_model.provider == "openai" or (
            current_model.provider == "openrouter"
            and current_model.model.startswith("openai/")
        ):
            tokenizer_model = current_model.model.split("/")[-1]
            is_approximate = False

    # Track token counts by category
    by_role: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)

    # Analyze each message (including hidden, since they're sent to the model)
    for msg in ctx.manager.log.messages:
        content_tokens = len_tokens(msg.content, tokenizer_model)

        # Count by role
        by_role[msg.role] += content_tokens

        # Categorize content type
        # Check for tool uses
        tool_uses = list(ToolUse.iter_from_content(msg.content))
        if tool_uses:
            by_type["tool_use"] += content_tokens
        # Check for thinking blocks (Anthropic uses <thinking> tags)
        elif "<thinking>" in msg.content or "<think>" in msg.content:
            by_type["thinking"] += content_tokens
        else:
            by_type["message"] += content_tokens

    # Calculate totals
    total_tokens = sum(by_role.values())

    # Display breakdown
    console.log("[bold]Token Usage by Role:[/bold]")
    for role in ["system", "user", "assistant"]:
        tokens = by_role.get(role, 0)
        pct = (tokens / total_tokens * 100) if total_tokens > 0 else 0
        console.log(f"  {role:10s}: {tokens:6,} ({pct:5.1f}%)")

    console.log("\n[bold]Token Usage by Type:[/bold]")
    for type_name, tokens in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        pct = (tokens / total_tokens * 100) if total_tokens > 0 else 0
        console.log(f"  {type_name:10s}: {tokens:6,} ({pct:5.1f}%)")

    console.log(f"\n[bold]Total Context:[/bold] {total_tokens:,} tokens")

    if is_approximate:
        console.log(f"[dim](approximate, using {tokenizer_model} tokenizer)[/dim]")


@command("tokens", aliases=["cost"])
def cmd_tokens(ctx: CommandContext) -> None:
    """Show token usage and costs.

    Shows session costs (current session) and conversation costs (all messages)
    when both are available. Falls back to approximation for old conversations.
    """
    from ..util.cost_display import (
        display_costs,
        gather_conversation_costs,
        gather_session_costs,
    )

    ctx.manager.undo(1, quiet=True)

    session = gather_session_costs()
    conversation = gather_conversation_costs(ctx.manager.log.messages)
    display_costs(session, conversation)


def _print_available_models() -> None:
    """Print all available models from all providers."""
    from ..llm.models import list_models  # fmt: skip

    list_models(dynamic_fetch=True)
