import atexit
import logging
from typing import cast

from dotenv import load_dotenv
from rich.logging import RichHandler

from .config import config_path, get_config, set_config_value
from .llm import get_model_from_api_key, guess_provider_from_config, init_llm
from .llm.models import (
    PROVIDERS,
    Provider,
    get_recommended_model,
    set_default_model,
)
from .tools import init_tools
from .util import console

logger = logging.getLogger(__name__)
_init_done = False


def init(model: str | None, interactive: bool, tool_allowlist: list[str] | None):
    global _init_done
    if _init_done:
        logger.warning("init() called twice, ignoring")
        return
    _init_done = True

    load_dotenv()
    init_model(model, interactive)
    init_tools(tool_allowlist)


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

    # ask user for API key
    if not model and interactive:
        model, _ = ask_for_api_key()

    # fail
    if not model:
        raise ValueError("No API key found, couldn't auto-detect provider")

    if any(model.startswith(f"{provider}/") for provider in PROVIDERS):
        provider, model = cast(tuple[Provider, str], model.split("/", 1))
    else:
        provider, model = cast(tuple[Provider, str], (model, None))

    # set up API_KEY and API_BASE, needs to be done before loading history to avoid saving API_KEY
    model = model or get_recommended_model(provider)
    model_full = f"{provider}/{model}"
    console.log(f"Using model: {model_full}")
    init_llm(provider)
    set_default_model(model_full)


def init_logging(verbose):
    handler = RichHandler()  # show_time=False
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
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


def _prompt_api_key() -> tuple[str, str, str]:  # pragma: no cover
    api_key = input("Your OpenAI, Anthropic, OpenRouter, or Gemini API key: ").strip()
    if (found_model_tuple := get_model_from_api_key(api_key)) is not None:
        return found_model_tuple
    else:
        console.print("Invalid API key format. Please try again.")
        return _prompt_api_key()


def ask_for_api_key():  # pragma: no cover
    """Interactively ask user for API key"""
    console.print("No API key set for OpenAI, Anthropic, OpenRouter, or Gemini.")
    console.print(
        """You can get one at:
 - OpenAI: https://platform.openai.com/account/api-keys
 - Anthropic: https://console.anthropic.com/settings/keys
 - OpenRouter: https://openrouter.ai/settings/keys
 - Gemini: https://aistudio.google.com/app/apikey
 """
    )
    # Save to config
    api_key, provider, env_var = _prompt_api_key()
    set_config_value(f"env.{env_var}", api_key)
    console.print(f"API key saved to config at {config_path}")
    console.print(f"Successfully set up {provider} API key.")
    return provider, api_key
