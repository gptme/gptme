"""Deprecated Anthropic models.

These models are no longer recommended but are kept for historical reference.
They are hidden from listings by default but still work when explicitly requested
via --model. Use `--include-deprecated` to include them in model listings.

Separated from models.py to keep the active model list clean.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import _ModelDictMeta  # fmt: skip

ANTHROPIC_MODELS_DEPRECATED: dict[str, "_ModelDictMeta"] = {
    # Claude 3.7 Sonnet (superseded by claude-sonnet-4+)
    "claude-3-7-sonnet-20250219": {
        "context": 200_000,
        "max_output": 8192,
        "price_input": 3,
        "price_output": 15,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 10, 1, tzinfo=timezone.utc),
        "deprecated": True,
    },
    # Claude 3.5 Sonnet (superseded by claude-sonnet-4+)
    "claude-3-5-sonnet-20241022": {
        "context": 200_000,
        "max_output": 8192,
        "price_input": 3,
        "price_output": 15,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2024, 4, 1, tzinfo=timezone.utc),
        "deprecated": True,
    },
    "claude-3-5-sonnet-20240620": {
        "context": 200_000,
        "max_output": 4096,
        "price_input": 3,
        "price_output": 15,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2024, 4, 1, tzinfo=timezone.utc),
        "deprecated": True,
    },
    # Claude 3.5 Haiku (superseded by claude-haiku-4-5)
    "claude-3-5-haiku-20241022": {
        "context": 200_000,
        "max_output": 8192,
        "price_input": 1,
        "price_output": 5,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2024, 4, 1, tzinfo=timezone.utc),
        "deprecated": True,
    },
    # Claude 3 Haiku (superseded by claude-3-5-haiku)
    "claude-3-haiku-20240307": {
        "context": 200_000,
        "max_output": 4096,
        "price_input": 0.25,
        "price_output": 1.25,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2024, 4, 1, tzinfo=timezone.utc),
        "deprecated": True,
    },
    # Claude 3 Opus (superseded by claude-opus-4+)
    "claude-3-opus-20240229": {
        "context": 200_000,
        "max_output": 4096,
        "price_input": 15,
        "price_output": 75,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2023, 8, 1, tzinfo=timezone.utc),
        "deprecated": True,
    },
    "claude-3-opus-latest": {
        "context": 200_000,
        "max_output": 4096,
        "price_input": 15,
        "price_output": 75,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2023, 8, 1, tzinfo=timezone.utc),
        "deprecated": True,  # resolves to claude-3-opus-20240229
    },
}
