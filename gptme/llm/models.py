import logging
from dataclasses import dataclass
from datetime import datetime
from typing import (
    Literal,
    TypedDict,
    cast,
    get_args,
)

from typing_extensions import NotRequired

from .llm_openai_models import OPENAI_MODELS

logger = logging.getLogger(__name__)

# available providers
Provider = Literal[
    "openai",
    "anthropic",
    "azure",
    "openrouter",
    "gemini",
    "groq",
    "xai",
    "deepseek",
    "nvidia",
    "requesty",
    "local",
]
PROVIDERS: list[Provider] = cast(list[Provider], get_args(Provider))
PROVIDERS_OPENAI: list[Provider]
PROVIDERS_OPENAI = [
    "openai",
    "azure",
    "openrouter",
    "gemini",
    "xai",
    "groq",
    "deepseek",
    "nvidia",
    "requesty",
    "local",
]


@dataclass(frozen=True)
class ModelMeta:
    provider: Provider | Literal["unknown"]
    model: str
    context: int
    max_output: int | None = None
    supports_streaming: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False  # models which support reasoning do not need prompting to use <thinking> tags

    # price in USD per 1M tokens
    # if price is not set, it is assumed to be 0
    price_input: float = 0
    price_output: float = 0

    knowledge_cutoff: datetime | None = None

    @property
    def full(self) -> str:
        return f"{self.provider}/{self.model}"


class _ModelDictMeta(TypedDict):
    context: int
    max_output: NotRequired[int]

    # price in USD per 1M tokens
    price_input: NotRequired[float]
    price_output: NotRequired[float]

    supports_streaming: NotRequired[bool]
    supports_vision: NotRequired[bool]
    supports_reasoning: NotRequired[bool]

    knowledge_cutoff: NotRequired[datetime]


# default model
DEFAULT_MODEL: ModelMeta | None = None

# known models metadata
# TODO: can we get this from the API?
MODELS: dict[Provider, dict[str, _ModelDictMeta]] = {
    "openai": OPENAI_MODELS,
    # https://docs.anthropic.com/en/docs/about-claude/models
    "anthropic": {
        "claude-3-7-sonnet-20250219": {
            "context": 200_000,
            # TODO: supports beta header `output-128k-2025-02-19` for 128k output option
            "max_output": 8192,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "knowledge_cutoff": datetime(2024, 10, 1),
        },
        "claude-3-5-sonnet-20241022": {
            "context": 200_000,
            "max_output": 8192,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-5-sonnet-20240620": {
            "context": 200_000,
            "max_output": 4096,
            "price_input": 3,
            "price_output": 15,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-5-haiku-20241022": {
            "context": 200_000,
            "max_output": 8192,
            "price_input": 1,
            "price_output": 5,
            "supports_vision": True,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-haiku-20240307": {
            "context": 200_000,
            "max_output": 4096,
            "price_input": 0.25,
            "price_output": 1.25,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-opus-20240229": {
            "context": 200_000,
            "max_output": 4096,
            "price_input": 15,
            "price_output": 75,
        },
    },
    # https://ai.google.dev/gemini-api/docs/models/gemini#gemini-1.5-flash
    # https://ai.google.dev/pricing#1_5flash
    "gemini": {
        "gemini-1.5-flash-latest": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            "price_output": 0.60,
            "supports_vision": True,
        },
        "gemini-2.0-flash-thinking-exp-01-21": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            "price_output": 0.60,
            "supports_vision": True,
        },
    },
    # https://api-docs.deepseek.com/quick_start/pricing
    "deepseek": {
        "deepseek-chat": {
            "context": 64_000,
            "max_output": 8192,
            # 10x better price for cache hits
            "price_input": 0.14,
            "price_output": 1.1,
        },
        "deepseek-reasoner": {
            "context": 64_000,
            "max_output": 8192,
            "price_input": 0.55,
            "price_output": 2.19,
        },
    },
    # https://groq.com/pricing/
    "groq": {
        "llama-3.3-70b-versatile": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.59,
            "price_output": 0.79,
        },
    },
    "xai": {
        "grok-beta": {
            "context": 131_072,
            "max_output": 4096,  # guess
            "price_input": 5,
            "price_output": 15,
        },
        "grok-vision-beta": {
            "context": 8192,
            "max_output": 4096,  # guess
            "price_input": 5,  # $10/1Mtok for vision
            "price_output": 15,
            "supports_vision": True,
        },
    },
    "openrouter": {
        "anthropic/claude-3.5-sonnet": {
            "context": 200_000,
            "max_output": 8192,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
        },
        "meta-llama/llama-3.3-70b-instruct": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.12,
            "price_output": 0.3,
        },
        "meta-llama/llama-3.1-405b-instruct": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.8,
            "price_output": 0.8,
        },
        "google/gemini-flash-1.5": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.075,
            "price_output": 0.3,
            "supports_vision": True,
        },
    },
    "requesty": {},
    "nvidia": {},
    "azure": {},
    "local": {},
}

# check that all providers have a MODELS entry
assert set(PROVIDERS) == set(MODELS.keys())


def get_default_model() -> ModelMeta | None:
    return DEFAULT_MODEL


def get_default_model_summary() -> ModelMeta | None:
    if not DEFAULT_MODEL:
        return None
    provider = DEFAULT_MODEL.provider
    assert provider != "unknown"
    return get_model(f"{provider}/{get_summary_model(provider)}")


def set_default_model(model: str | ModelMeta) -> None:
    modelmeta = model if isinstance(model, ModelMeta) else get_model(model)
    assert modelmeta
    global DEFAULT_MODEL
    DEFAULT_MODEL = modelmeta


_logged_warnings = set()


def log_warn_once(msg: str):
    if msg not in _logged_warnings:
        logger.warning(msg)
        _logged_warnings.add(msg)


def get_model(model: str) -> ModelMeta:
    # if only provider is given, get recommended model
    if model in PROVIDERS:
        provider = cast(Provider, model)
        model = get_recommended_model(provider)
        return get_model(f"{provider}/{model}")

    if any(f"{provider}/" in model for provider in PROVIDERS):
        provider, model = cast(tuple[Provider, str], model.split("/", 1))
        if provider not in MODELS or model not in MODELS[provider]:
            if provider not in ["openrouter", "local"]:
                log_warn_once(
                    f"Unknown model: using fallback metadata for {provider}/{model}"
                )
            return ModelMeta(provider, model, context=128_000)
    else:
        # try to find model in all providers
        for provider in MODELS:
            if model in MODELS[provider]:
                break
        else:
            logger.warning(f"Unknown model {model}, using fallback metadata")
            return ModelMeta(provider="unknown", model=model, context=128_000)

    return ModelMeta(provider, model, **MODELS[provider][model])


def get_recommended_model(provider: Provider) -> str:  # pragma: no cover
    if provider == "openai":
        return "gpt-4o"
    elif provider == "openrouter":
        return "meta-llama/llama-3.1-405b-instruct"
    elif provider == "gemini":
        return "gemini-1.5-flash-latest"
    elif provider == "anthropic":
        return "claude-3-7-sonnet-20250219"
    else:
        raise ValueError(f"Provider {provider} did not have a recommended model")


def get_summary_model(provider: Provider) -> str:  # pragma: no cover
    if provider == "openai":
        return "gpt-4o-mini"
    elif provider == "openrouter":
        return "meta-llama/llama-3.1-8b-instruct"
    elif provider == "gemini":
        return "gemini-1.5-flash-latest"
    elif provider == "anthropic":
        return "claude-3-haiku-20240307"
    elif provider == "deepseek":
        return "deepseek-chat"
    else:
        raise ValueError(f"Provider {provider} did not have a summary model")
