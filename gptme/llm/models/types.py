import re
from dataclasses import dataclass
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Literal,
    TypedDict,
    cast,
    get_args,
)

from typing_extensions import NotRequired

if TYPE_CHECKING:
    from ...tools.base import ToolFormat

# Pattern to match date suffixes like -20250929 or -20250514
_DATE_SUFFIX_PATTERN = re.compile(r"-\d{8}$")

# Pattern to extract the model family prefix (letters and hyphens before a version number)
# e.g. "claude-sonnet-4-6" -> "claude-sonnet", "gpt-5-mini" -> "gpt-", "grok-4-1-fast" -> "grok-"
_MODEL_FAMILY_PATTERN = re.compile(r"^([a-z]+-?[a-z]*)")

# Model aliases: maps short alias names to their canonical model IDs per provider.
# Avoids duplicating full metadata entries for models with both short and dated names.
MODEL_ALIASES: dict[str, dict[str, str]] = {
    "anthropic": {
        "claude-opus-4-1": "claude-opus-4-1-20250805",
        "claude-opus-4-0": "claude-opus-4-20250514",
        "claude-sonnet-4-0": "claude-sonnet-4-20250514",
        "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
        "claude-opus-4-5": "claude-opus-4-5-20251101",
        "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    },
}

# Built-in providers (static list)
BuiltinProvider = Literal[
    "openai",
    "openai-subscription",
    "anthropic",
    "azure",
    "openrouter",
    "gptme",
    "gemini",
    "groq",
    "xai",
    "deepseek",
    "nvidia",
    "local",
]
PROVIDERS: list[BuiltinProvider] = cast(
    list[BuiltinProvider], get_args(BuiltinProvider)
)


class CustomProvider(str):
    """Represents a custom provider configured by the user.

    Subclasses str so it can be used anywhere a provider string is expected,
    but is distinguishable from plain strings and built-in Provider literals.
    """


def is_custom_provider(provider: str) -> bool:
    """Check if the provider is a custom provider configured by the user."""
    from ...config import get_config  # fmt: skip

    config = get_config()
    return any(p.name == provider for p in config.user.providers)


# Type alias for any provider (built-in or custom)
Provider = BuiltinProvider | CustomProvider

PROVIDERS_OPENAI: list[BuiltinProvider]
PROVIDERS_OPENAI = [
    "openai",
    "azure",
    "openrouter",
    "gptme",
    "gemini",
    "xai",
    "groq",
    "deepseek",
    "nvidia",
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
    supports_parallel_tool_calls: bool = (
        False  # models that can emit multiple tool calls in a single response
    )

    # price in USD per 1M tokens
    # if price is not set, it is assumed to be 0
    price_input: float = 0
    price_output: float = 0

    knowledge_cutoff: datetime | None = None

    # whether the model is deprecated/sunset by the provider
    deprecated: bool = False

    # preferred tool format for this model (used as fallback when not explicitly set)
    default_tool_format: "ToolFormat | None" = None

    @property
    def full(self) -> str:
        # For unknown providers (including custom providers), the model field
        # already contains the full qualified name
        if self.provider == "unknown":
            return self.model
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
    supports_parallel_tool_calls: NotRequired[bool]

    knowledge_cutoff: NotRequired[datetime]
    deprecated: NotRequired[bool]

    # preferred tool format for this model
    default_tool_format: NotRequired["ToolFormat"]
