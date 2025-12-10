import atexit
import logging
from typing import cast

from dotenv import load_dotenv
from rich.logging import RichHandler

from .commands import init_commands
from .config import get_config
from .hooks import init_hooks
from .llm import guess_provider_from_config, init_llm, is_custom_provider
from .llm.models import (
    PROVIDERS,
    Provider,
    get_model,
    get_recommended_model,
    set_default_model,
)
from .setup import ask_for_api_key
from .tools import ToolFormat, init_tools, set_tool_format
from .util import console

logger = logging.getLogger(__name__)
_init_done = False


def init(
    model: str | None,
    interactive: bool,
    tool_allowlist: list[str] | None,
    tool_format: ToolFormat,
):
    global _init_done
    if _init_done:
        logger.warning("init() called twice, ignoring")
        return
    _init_done = True

    load_dotenv()
    init_model(model, interactive)
    init_tools(tool_allowlist)
    init_hooks()
    init_commands()

    set_tool_format(tool_format)


def init_model(
    model: str | None = None,
    interactive: bool = False,
):
    config = get_config()

    # get from config
    if not model:
        model = (config.chat.model if config.chat else None) or config.get_env("MODEL")

    if not model:  # pragma: no cover
        # auto-detect depending on if OPENAI_API_KEY or ANTHROPIC_API_KEY is set
        model = guess_provider_from_config()
        if not model:
            console.print("[yellow]No API keys set, no provider available.[/yellow]")

    # ask user for API key
    if not model and interactive:
        model, _ = ask_for_api_key()

    # fail
    if not model:
        raise ValueError("No API key found, couldn't auto-detect provider")

    # Check if model has provider/model format
    if "/" in model:
        provider_part = model.split("/")[0]
        # Check if it's a built-in provider or custom provider
        if provider_part in PROVIDERS:
            provider, model_name = cast(tuple[Provider, str], model.split("/", 1))
        elif is_custom_provider(provider_part):
            # Custom provider - use full model string, provider is extracted
            provider = provider_part  # type: ignore[assignment]
            model_name = "/".join(model.split("/")[1:])  # Rest after provider
        else:
            # Unknown provider format, treat as provider only
            provider, model_name = cast(tuple[Provider, str], (model, None))
    else:
        # No slash - check if it's a custom provider with default model
        if is_custom_provider(model):
            # Get the ModelMeta which will resolve the default model
            model_meta = get_model(model)
            provider = model  # type: ignore[assignment]
            model_name = "/".join(
                model_meta.model.split("/")[1:]
            )  # Strip provider prefix
        else:
            provider, model_name = cast(tuple[Provider, str], (model, None))

    # set up API_KEY and API_BASE, needs to be done before loading history to avoid saving API_KEY
    if model_name is None:
        model_name = get_recommended_model(provider)  # type: ignore[arg-type]
    model_full = f"{provider}/{model_name}"
    console.log(f"Using model: [green]{model_full}[/green]")
    init_llm(provider)
    set_default_model(model_full)


def init_logging(verbose):
    handler = RichHandler()  # show_time=False
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
        force=True,  # Override any previous logging configuration
    )

    # anthropic spams debug logs for every request
    logging.getLogger("anthropic").setLevel(logging.INFO)
    logging.getLogger("openai").setLevel(logging.INFO)
    # set httpx logging to WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Register cleanup handler

    def cleanup_logging():
        logging.getLogger().removeHandler(handler)
        logging.shutdown()

    atexit.register(cleanup_logging)
