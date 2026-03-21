from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .llm_openai_models_deprecated import OPENAI_MODELS_DEPRECATED

if TYPE_CHECKING:
    from .models import _ModelDictMeta  # fmt: skip

# Active models only. Deprecated models are in llm_openai_models_deprecated.py
# and merged in below. They still work when explicitly requested via --model.

_OPENAI_MODELS_ACTIVE: dict[str, "_ModelDictMeta"] = {
    # GPT-5
    "gpt-5": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.25,  # $0.13 for cached inputs
        "price_output": 10,
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_parallel_tool_calls": True,
        "knowledge_cutoff": datetime(2024, 9, 30, tzinfo=timezone.utc),
    },
    "gpt-5-mini": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 0.25,  # $0.025 for cached inputs
        "price_output": 2,
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_parallel_tool_calls": True,
        "knowledge_cutoff": datetime(2024, 5, 31, tzinfo=timezone.utc),
    },
    "gpt-5-nano": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 0.05,  # $0.005 for cached inputs
        "price_output": 0.4,
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_parallel_tool_calls": True,
        "knowledge_cutoff": datetime(2024, 5, 31, tzinfo=timezone.utc),
    },
    # GPT-4.1
    "gpt-4.1": {
        "context": 1_047_576,
        "max_output": 32_768,
        "price_input": 2,
        "price_output": 8,
        "supports_vision": True,
        "supports_parallel_tool_calls": True,
        "knowledge_cutoff": datetime(2024, 6, 1, tzinfo=timezone.utc),
    },
    "gpt-4.1-mini": {
        "context": 1_047_576,
        "max_output": 32_768,
        "price_input": 0.4,
        "price_output": 1.6,
        "supports_vision": True,
        "supports_parallel_tool_calls": True,
        "knowledge_cutoff": datetime(2024, 6, 1, tzinfo=timezone.utc),
    },
    "gpt-4.1-nano": {
        "context": 1_047_576,
        "max_output": 32_768,
        "price_input": 0.1,
        "price_output": 0.4,
        "supports_vision": True,
        "supports_parallel_tool_calls": True,
        "knowledge_cutoff": datetime(2024, 6, 1, tzinfo=timezone.utc),
    },
    # GPT-4o
    "gpt-4o": {
        "context": 128_000,
        "price_input": 5,
        "price_output": 15,
        "supports_vision": True,
        "supports_parallel_tool_calls": True,
        # October 2023
        "knowledge_cutoff": datetime(2023, 10, 1, tzinfo=timezone.utc),
    },
    # GPT-4o mini
    "gpt-4o-mini": {
        "context": 128_000,
        "price_input": 0.15,
        "price_output": 0.6,
        "supports_vision": True,
        "knowledge_cutoff": datetime(2023, 10, 1, tzinfo=timezone.utc),
    },
    # OpenAI o4-mini
    "o4-mini": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 1.1,
        "price_output": 4.4,
        "supports_vision": True,
        "supports_reasoning": True,
    },
    # OpenAI o3
    "o3": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 2,
        "price_output": 8,
        "supports_vision": True,
        "supports_reasoning": True,
    },
    "o3-mini": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 1.1,
        "price_output": 4.4,
        "supports_reasoning": True,
    },
    # OpenAI o1
    "o1": {
        "context": 200_000,
        "max_output": 100_000,
        "price_input": 15,
        "price_output": 60,
        "supports_reasoning": True,
    },
}

# Merge active + deprecated into the public dict
OPENAI_MODELS: dict[str, "_ModelDictMeta"] = {
    **_OPENAI_MODELS_ACTIVE,
    **OPENAI_MODELS_DEPRECATED,
}

# OpenAI Codex / Responses API models.
# These models use the Responses API (not Chat Completions), and are accessed
# via the ChatGPT subscription (openai-subscription provider). Prices reflect
# API-equivalent cost for comparison. models.py adds default_tool_format="tool".
# Reasoning level suffix (e.g., :high) is stripped at lookup time in get_model().
OPENAI_SUBSCRIPTION_MODELS: dict[str, "_ModelDictMeta"] = {
    # GPT-5.4 — latest flagship, 1M context
    "gpt-5.4": {
        "context": 1_050_000,
        "max_output": 128_000,
        "price_input": 2.5,
        "price_output": 15,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2025, 8, 31, tzinfo=timezone.utc),
    },
    # GPT-5.3 Codex — top-tier agentic coding model
    "gpt-5.3-codex": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.75,
        "price_output": 14,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2025, 8, 31, tzinfo=timezone.utc),
    },
    # GPT-5.3 Codex Spark — fast text-only coding (1000+ tok/s)
    "gpt-5.3-codex-spark": {
        "context": 128_000,
        "max_output": 128_000,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2025, 8, 31, tzinfo=timezone.utc),
    },
    # GPT-5.2
    "gpt-5.2": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.75,
        "price_output": 14,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2025, 8, 31, tzinfo=timezone.utc),
    },
    # GPT-5.2 Codex — agentic coding variant of 5.2
    "gpt-5.2-codex": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.75,
        "price_output": 14,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2025, 8, 31, tzinfo=timezone.utc),
    },
    # GPT-5.1 Codex Max — multi-context-window compaction
    "gpt-5.1-codex-max": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.25,
        "price_output": 10,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 9, 30, tzinfo=timezone.utc),
    },
    # GPT-5.1 Codex — agentic coding variant of 5.1
    "gpt-5.1-codex": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.25,
        "price_output": 10,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 9, 30, tzinfo=timezone.utc),
    },
    # GPT-5.1 Codex Mini — smaller/cheaper coding variant
    "gpt-5.1-codex-mini": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 0.25,
        "price_output": 2,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 9, 30, tzinfo=timezone.utc),
    },
    # GPT-5.1
    "gpt-5.1": {
        "context": 400_000,
        "max_output": 128_000,
        "price_input": 1.25,
        "price_output": 10,
        "supports_vision": True,
        "supports_reasoning": True,
        "knowledge_cutoff": datetime(2024, 9, 30, tzinfo=timezone.utc),
    },
}
