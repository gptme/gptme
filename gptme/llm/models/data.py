from datetime import datetime, timezone

from ..llm_anthropic_models_deprecated import ANTHROPIC_MODELS_DEPRECATED
from ..llm_openai_models import OPENAI_MODELS, OPENAI_SUBSCRIPTION_MODELS
from .types import PROVIDERS, Provider, _ModelDictMeta

# TODO: can we get this from the API?
MODELS: dict[Provider, dict[str, _ModelDictMeta]] = {
    "openai": OPENAI_MODELS,
    # OpenAI Subscription (ChatGPT Plus/Pro via Codex backend)
    # Uses the Responses API (not Chat Completions). Per-model specs from
    # llm_openai_models.py; prices reflect API-equivalent cost for comparison.
    # Reasoning level suffix (e.g., :high) is stripped at lookup time in get_model()
    "openai-subscription": {
        model: {**props, "default_tool_format": "tool"}
        for model, props in OPENAI_SUBSCRIPTION_MODELS.items()
    },
    # https://docs.anthropic.com/en/docs/about-claude/models
    # Active models here; deprecated models in llm_anthropic_models_deprecated.py
    "anthropic": {
        "claude-opus-4-7": {
            "context": 1_000_000,
            "max_output": 128_000,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 5,
            "price_output": 25,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(
                2025, 8, 1, tzinfo=timezone.utc
            ),  # training cutoff Aug 2025
        },
        "claude-opus-4-6": {
            "context": 1_000_000,
            "max_output": 128_000,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 5,
            "price_output": 25,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(
                2025, 8, 1, tzinfo=timezone.utc
            ),  # training cutoff Aug 2025, reliable May 2025
        },
        "claude-sonnet-4-6": {
            "context": 1_000_000,
            "max_output": 64_000,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,  # verified: emits multiple tool calls per response
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),  # training cutoff Jan 2026, reliable Aug 2025
        },
        "claude-opus-4-5-20251101": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 5,
            "price_output": 25,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(
                2025, 8, 1, tzinfo=timezone.utc
            ),  # training cutoff Aug 2025, reliable May 2025
        },
        "claude-sonnet-4-5-20250929": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(
                2025, 7, 1, tzinfo=timezone.utc
            ),  # training cutoff Jul 2025, reliable Jan 2025
        },
        "claude-haiku-4-5-20251001": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 1,
            "price_output": 5,
            "supports_vision": True,
            "supports_reasoning": True,
            # supports_parallel_tool_calls intentionally absent (defaults to False):
            # unlike Sonnet/Opus 4.5, Haiku 4.5 does not emit multiple tool calls per response
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(
                2025, 7, 1, tzinfo=timezone.utc
            ),  # "reliable cutoff" is Feb 2025
        },
        "claude-opus-4-1-20250805": {
            "context": 200_000,
            "max_output": 32_000,
            "price_input": 15,
            "price_output": 75,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(2025, 3, 1, tzinfo=timezone.utc),
        },
        "claude-opus-4-20250514": {
            "context": 200_000,
            "max_output": 32_000,
            "price_input": 15,
            "price_output": 75,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(2025, 3, 1, tzinfo=timezone.utc),
        },
        "claude-sonnet-4-20250514": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
            "knowledge_cutoff": datetime(2025, 3, 1, tzinfo=timezone.utc),
        },
        # Deprecated models merged from separate file
        **ANTHROPIC_MODELS_DEPRECATED,
    },
    # https://ai.google.dev/gemini-api/docs/models
    # https://ai.google.dev/gemini-api/docs/pricing
    "gemini": {
        "gemini-3.1-pro-preview": {
            "context": 1_000_000,
            "max_output": 64_000,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 2,
            "price_output": 12,
            "supports_vision": True,
            "supports_reasoning": True,
        },
        "gemini-3-pro-preview": {
            "context": 1_000_000,
            "max_output": 64_000,
            "price_input": 2,
            "price_output": 12,
            "supports_vision": True,
            "supports_reasoning": True,
        },
        "gemini-3-flash-preview": {
            "context": 1_000_000,
            "max_output": 64_000,
            "price_input": 0.5,
            "price_output": 3,
            "supports_vision": True,
            "supports_reasoning": True,
        },
        "gemini-2.0-flash": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.10,
            "price_output": 0.40,
            "supports_vision": True,
        },
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
            "supports_reasoning": True,
        },
        "gemini-2.0-flash-lite": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.075,
            "price_output": 0.30,
        },
        "gemini-2.5-flash-preview-04-17": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            # NOTE: $3.5/Mtok for thinking tokens
            "price_output": 0.60,
            "supports_vision": True,
            "supports_reasoning": True,
        },
        "gemini-2.5-pro-preview-05-06": {
            "context": 1_048_576,
            "max_output": 65_536,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 1.25,
            "price_output": 10,
            "supports_vision": True,
            "supports_reasoning": True,
        },
        "gemini-2.5-flash-lite": {
            "context": 1_000_000,
            "max_output": 64_000,
            "price_input": 0.1,
            "price_output": 0.4,
            "supports_vision": True,
        },
        "gemini-2.5-flash": {
            "context": 1_048_576,
            "max_output": 65_536,
            "price_input": 0.3,
            "price_output": 2.5,
            "supports_vision": True,
            "supports_reasoning": True,
        },
        "gemini-2.5-pro": {
            "context": 1_048_576,
            "max_output": 65_536,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 1.25,
            "price_output": 10,
            "supports_vision": True,
            "supports_reasoning": True,
        },
    },
    # https://api-docs.deepseek.com/quick_start/pricing
    "deepseek": {
        "deepseek-chat": {
            "context": 128_000,
            "max_output": 8192,
            # 10x better price for cache hits
            "price_input": 0.14,
            "price_output": 1.1,
            "preferred_edit_format": "diff",
        },
        "deepseek-reasoner": {
            "context": 128_000,
            "max_output": 8192,
            "price_input": 0.55,
            "price_output": 2.19,
            "preferred_edit_format": "diff",
        },
    },
    # https://groq.com/pricing/
    "groq": {
        "llama-3.3-70b-versatile": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.59,
            "price_output": 0.79,
            "preferred_edit_format": "diff",
        },
    },
    # https://docs.x.ai/docs/models
    "xai": {
        "grok-4-1-fast": {
            "context": 2_000_000,
            "max_output": 30_000,
            "price_input": 0.2,
            "price_output": 0.5,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "grok-code-fast-1": {
            "context": 256_000,
            "max_output": 10_000,
            "price_input": 0.2,
            "price_output": 1.5,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "grok-4-fast": {
            "context": 2_000_000,
            "max_output": 30_000,
            "price_input": 0.2,
            "price_output": 0.5,
            "supports_reasoning": True,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
        "grok-4": {
            "context": 256_000,
            "max_output": 256_000,
            "price_input": 3,
            "price_output": 15,
            "supports_reasoning": True,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
        "grok-3": {
            "context": 131_072,
            "max_output": 131_072,
            "price_input": 3,
            "price_output": 15,
            "supports_reasoning": True,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
        "grok-3-mini": {
            "context": 131_072,
            "max_output": 131_072,
            "price_input": 0.3,
            "price_output": 0.5,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "grok-2-vision-1212": {
            "context": 32_768,
            "max_output": 32_768,
            "price_input": 2,
            "price_output": 10,
            "supports_vision": True,
            "preferred_edit_format": "whole",
        },
    },
    "openrouter": {
        "qwen/qwen3-max": {
            "context": 256_000,
            "max_output": 8192,
            "price_input": 1.2,
            "price_output": 6.0,
            "supports_vision": True,
        },
        "mistralai/magistral-medium-2506": {
            "context": 41_000,
            "max_output": 40_000,
            "price_input": 2,
            "price_output": 5,
            # "supports_vision": True,
            "supports_reasoning": True,
        },
        "anthropic/claude-opus-4.7": {
            "context": 1_000_000,
            "max_output": 128_000,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 5,
            "price_output": 25,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
        },
        "anthropic/claude-sonnet-4.6": {
            "context": 1_000_000,
            "max_output": 64_000,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
        },
        "anthropic/claude-haiku-4.5": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 1,
            "price_output": 5,
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
        "moonshotai/kimi-k2": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.38,
            "price_output": 1.52,
            "supports_vision": True,
        },
        "moonshotai/kimi-k2-0905": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.38,
            "price_output": 1.52,
            "supports_vision": True,
        },
    },
    "nvidia": {},
    "azure": {},
    # gptme managed service — proxies to multiple providers
    # Models are pass-through: gptme/claude-sonnet-4-6 → proxied to backend
    # Empty dict = models fetched dynamically or specified by user
    "gptme": {},
    "local": {},
}

# check that all providers have a MODELS entry
assert set(PROVIDERS) == set(MODELS.keys())
