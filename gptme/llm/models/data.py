from datetime import datetime, timezone
from typing import Literal

from ..llm_anthropic_models_deprecated import ANTHROPIC_MODELS_DEPRECATED
from ..llm_openai_models import OPENAI_MODELS, OPENAI_SUBSCRIPTION_MODELS
from .types import PROVIDERS, Provider, _ModelDictMeta


def _mark_subscription(models: dict[str, _ModelDictMeta]) -> dict[str, _ModelDictMeta]:
    """Mark all models in a dict as subscription-priced (zero marginal USD cost)."""
    return {
        name: {**props, "pricing_type": "subscription"}
        for name, props in models.items()
    }


def _set_tool_format(
    models: dict[str, _ModelDictMeta], tool_format: Literal["markdown", "xml", "tool"]
) -> dict[str, _ModelDictMeta]:
    """Stamp a default_tool_format on all models that don't already have one."""
    return {
        name: props
        if props.get("default_tool_format")
        else {**props, "default_tool_format": tool_format}
        for name, props in models.items()
    }


def _mark_parallel(models: dict[str, _ModelDictMeta]) -> dict[str, _ModelDictMeta]:
    """Stamp supports_parallel_tool_calls=True unless the model already sets it.

    An explicit per-model value still wins, so a later model that does *not*
    support parallel can opt out by setting the key to False.
    """
    return {
        name: props
        if "supports_parallel_tool_calls" in props
        else {**props, "supports_parallel_tool_calls": True}
        for name, props in models.items()
    }


# Providers that route through the OpenAI-compatible function-calling API — stamp
# default_tool_format="tool" on every model that doesn't already have one set.
# Anthropic and mock are excluded: anthropic uses the Anthropic SDK (not OpenAI-compat),
# and mock models are test-only stubs that don't need a tool format preference.
# Exported (no leading underscore) so resolution.py can apply it to dynamic fallbacks.
OPENAI_COMPAT_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "openai-subscription",
        "gemini",
        "deepseek",
        "groq",
        "xai",
        "grok-subscription",
        "moonshot",
        "requesty",
        "openrouter",
        "nvidia",
        "azure",
        "local",
        # gptme.ai proxies to various backends, but the client itself talks to it
        # via the OpenAI-compatible API (see llm_openai.py) — same fallback applies
        # when dynamic fetch fails/misses and no static registry entry exists.
        "gptme",
    }
)

# Providers whose official docs state that current models can emit multiple
# tool calls in one response. Applied at MODELS construction so it cannot
# drift from the static dicts. Mixed providers (openrouter, groq) are stamped
# per-model instead: Groq's own table is model-specific (llama-3.3 Yes,
# gpt-oss No), and OpenRouter aliases a mix of backends.
# Docs:
#   gemini: https://ai.google.dev/gemini-api/docs/function-calling#parallel_function_calling
#   xai / grok-subscription: https://docs.x.ai/developers/tools/function-calling#parallel-function-calling
#   deepseek: https://api-docs.deepseek.com/news/news0725/
PARALLEL_TOOL_PROVIDERS: frozenset[str] = frozenset(
    {
        "gemini",
        "deepseek",
        "xai",
        "grok-subscription",
    }
)

# TODO: can we get this from the API?
_MODELS_RAW: dict[Provider, dict[str, _ModelDictMeta]] = {
    "openai": OPENAI_MODELS,
    # OpenAI Subscription (ChatGPT Plus/Pro via Codex backend)
    # Uses the Responses API (not Chat Completions). Per-model specs from
    # llm_openai_models.py; prices reflect API-equivalent cost for comparison.
    # Reasoning level suffix (e.g., :high) is stripped at lookup time in get_model()
    "openai-subscription": _mark_subscription(
        {
            model: {**props, "default_tool_format": "tool"}
            for model, props in OPENAI_SUBSCRIPTION_MODELS.items()
        }
    ),
    # https://docs.anthropic.com/en/docs/about-claude/models
    # Active models here; deprecated models in llm_anthropic_models_deprecated.py
    "anthropic": {
        "claude-opus-4-8": {
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
                2026, 1, 1, tzinfo=timezone.utc
            ),  # training cutoff Jan 2026
        },
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
            "preferred_edit_format": "diff",
        },
        "gemini-3-pro-preview": {
            "context": 1_000_000,
            "max_output": 64_000,
            "price_input": 2,
            "price_output": 12,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "gemini-3-flash-preview": {
            "context": 1_000_000,
            "max_output": 64_000,
            "price_input": 0.5,
            "price_output": 3,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "gemini-2.0-flash": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.10,
            "price_output": 0.40,
            "supports_vision": True,
            "preferred_edit_format": "whole",
        },
        "gemini-1.5-flash-latest": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            "price_output": 0.60,
            "supports_vision": True,
            "preferred_edit_format": "whole",
        },
        "gemini-2.0-flash-thinking-exp-01-21": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            "price_output": 0.60,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "gemini-2.0-flash-lite": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.075,
            "price_output": 0.30,
            "preferred_edit_format": "whole",
        },
        "gemini-2.5-flash-preview-04-17": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            # NOTE: $3.5/Mtok for thinking tokens
            "price_output": 0.60,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "gemini-2.5-pro-preview-05-06": {
            "context": 1_048_576,
            "max_output": 65_536,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 1.25,
            "price_output": 10,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "gemini-2.5-flash-lite": {
            "context": 1_000_000,
            "max_output": 64_000,
            "price_input": 0.1,
            "price_output": 0.4,
            "supports_vision": True,
            "preferred_edit_format": "whole",
        },
        "gemini-2.5-flash": {
            "context": 1_048_576,
            "max_output": 65_536,
            "price_input": 0.3,
            "price_output": 2.5,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
        "gemini-2.5-pro": {
            "context": 1_048_576,
            "max_output": 65_536,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 1.25,
            "price_output": 10,
            "supports_vision": True,
            "supports_reasoning": True,
            "preferred_edit_format": "diff",
        },
    },
    # https://api-docs.deepseek.com/quick_start/pricing
    # Parallel: stamped via PARALLEL_TOOL_PROVIDERS.
    # `strict` mode exists but requires base_url=.../beta, which gptme does not
    # use — leave supports_strict_tools unset rather than send a flag the
    # production endpoint may reject.
    # https://api-docs.deepseek.com/guides/tool_calls/#strict-mode-beta
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
            "supports_reasoning": True,
        },
    },
    # https://groq.com/pricing/
    # Parallel tool use is model-specific on Groq (llama-3.3-70b-versatile Yes,
    # openai/gpt-oss-* No). Stamp per-model, not via PARALLEL_TOOL_PROVIDERS.
    # https://console.groq.com/docs/tool-use/overview#supported-models
    "groq": {
        "llama-3.3-70b-versatile": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.59,
            "price_output": 0.79,
            "preferred_edit_format": "diff",
            "supports_parallel_tool_calls": True,
        },
    },
    # https://docs.x.ai/docs/models
    # SuperGrok/SuperGrok-Heavy subscription (grok.com) via xAI API.
    # Uses OAuth tokens from the grok CLI (~/.grok/auth.json) — $0 marginal.
    # Prices reflect xAI API-equivalent cost for comparison purposes.
    # Auth: run `grok login` or `gptme auth grok-subscription`.
    # Parallel: stamped via PARALLEL_TOOL_PROVIDERS (xAI default).
    # Tool-arg schemas are implicitly strict; gptme's supports_strict_tools flag
    # sends OpenAI `strict=True`, which xAI does not document as an accepted
    # request field, so that flag stays unset.
    "grok-subscription": _mark_subscription(
        {
            # grok-4.6 — current frontier model on SuperGrok subscription and
            # the grok CLI default (grok CLI 0.2.117, 2026-08).
            # https://docs.x.ai/developers/models/grok-4.6 — 500K context,
            # text+image input, reasoning, function calling, structured outputs.
            # $2/$6 per 1M below 200K prompt tokens ($4/$12 above; $0.50 cached).
            "grok-4.6": {
                "context": 500_000,
                "max_output": 128_000,
                "price_input": 2,
                "price_output": 6,
                "supports_vision": True,
                "supports_reasoning": True,
                "preferred_edit_format": "diff",
            },
            # grok-4.5 — previous frontier model available on SuperGrok subscription
            # https://x.ai/blog/grok-4-5 (500K context, reasoning support)
            "grok-4.5": {
                "context": 500_000,
                "max_output": 128_000,
                "price_input": 2,
                "price_output": 6,
                "supports_vision": True,
                "supports_reasoning": True,
                "preferred_edit_format": "diff",
            },
        }
    ),
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
            "preferred_edit_format": "whole",
        },
        "anthropic/claude-opus-4.8": {
            "context": 1_000_000,
            "max_output": 128_000,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 5,
            "price_output": 25,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,
            "preferred_edit_format": "diff",
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
            "preferred_edit_format": "diff",
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
            "preferred_edit_format": "diff",
        },
        "anthropic/claude-haiku-4.5": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 1,
            "price_output": 5,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
        "meta-llama/llama-3.3-70b-instruct": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.12,
            "price_output": 0.3,
            "preferred_edit_format": "diff",
        },
        "google/gemini-3.5-flash": {
            "context": 1_048_576,
            "max_output": 65_536,
            # Pricing via OpenRouter (2026-05-20 launch)
            "price_input": 1.5,
            "price_output": 9,
            "supports_vision": True,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,  # Gemini parallel function calling
            "preferred_edit_format": "diff",
        },
        "moonshotai/kimi-k2": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.38,
            "price_output": 1.52,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
        "moonshotai/kimi-k2-0905": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.38,
            "price_output": 1.52,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
        # https://openrouter.ai/deepseek/deepseek-v4-pro (pricing verified 2026-05-30)
        # MIT-licensed open-weight model, ~80.6% SWE-bench Verified
        "deepseek/deepseek-v4-pro": {
            "context": 1_000_000,
            "max_output": 32_768,
            "price_input": 0.435,
            "price_output": 0.87,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,  # DeepSeek API supports parallel tool calls
            "preferred_edit_format": "diff",
        },
        "deepseek/deepseek-v4-flash": {
            "context": 1_000_000,
            "max_output": 32_768,
            "price_input": 0.0983,
            "price_output": 0.1966,
            "supports_reasoning": True,
            "supports_parallel_tool_calls": True,  # DeepSeek API supports parallel tool calls
            "preferred_edit_format": "diff",
        },
    },
    "moonshot": {
        # https://platform.kimi.ai/docs/models
        # https://platform.kimi.ai/docs/api/chat
        # Kimi docs list 256K context for k2.6/k2.5. max_completion_tokens
        # is constrained by input + output fitting within that context; the
        # 32K K2.5 guide value is a default, not a documented hard output cap.
        "kimi-k3": {
            "context": 1_048_576,
            "max_output": 1_048_576,
            "price_input": 3.00,
            "price_output": 15.00,
            "supports_reasoning": True,
            "supports_vision": True,
            "supports_parallel_tool_calls": True,
            "supports_strict_tools": True,
            "preferred_edit_format": "diff",
        },
        "kimi-k2.6": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.60,
            "price_output": 2.40,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
        "kimi-k2": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.38,
            "price_output": 1.52,
            "supports_vision": True,
            "preferred_edit_format": "diff",
        },
    },
    "nvidia": {},
    "azure": {},
    # Requesty — OpenAI-compatible LLM gateway, provider/model naming.
    # Model keys use the full sub-provider/model path (everything after "requesty/").
    "requesty": {
        "openai/gpt-4o-mini": {
            "context": 128_000,
            "price_input": 0.15,
            "price_output": 0.6,
            "supports_vision": True,
            "preferred_edit_format": "whole",
        },
    },
    # gptme managed service — proxies to multiple providers
    # Models are pass-through: gptme/claude-sonnet-4-6 → proxied to backend
    # Empty dict = models fetched dynamically or specified by user
    "gptme": {},
    "local": {},
    # Built-in offline mock provider — no auth, deterministic canned responses.
    # For tests, demos, and offline development. See gptme/llm/llm_mock.py.
    "mock": {
        # Echoes the last user message back (round-trip / plumbing tests).
        "echo": {
            "context": 128_000,
            "max_output": 4096,
            "price_input": 0,
            "price_output": 0,
        },
        # Returns a fixed canned string (deterministic output tests).
        "static": {
            "context": 128_000,
            "max_output": 4096,
            "price_input": 0,
            "price_output": 0,
        },
    },
}

# check that all providers have a _MODELS_RAW entry
assert set(PROVIDERS) == set(_MODELS_RAW.keys())


def _stamp_provider(
    provider: str, models: dict[str, _ModelDictMeta]
) -> dict[str, _ModelDictMeta]:
    """Apply construction-time stamps so static dicts and MODELS stay consistent."""
    if provider in PARALLEL_TOOL_PROVIDERS:
        models = _mark_parallel(models)
    if provider in OPENAI_COMPAT_PROVIDERS:
        models = _set_tool_format(models, "tool")
    return models


# Stamp default_tool_format="tool" on all OpenAI-compatible providers, and
# supports_parallel_tool_calls on PARALLEL_TOOL_PROVIDERS, at construction
# time. Building MODELS in one step (rather than reassigning it) ensures that any
# code reading the intermediate dicts (OPENAI_MODELS, etc.) and any code reading
# MODELS see a consistent value with no ordering hazard.
MODELS = {
    provider: _stamp_provider(provider, models)
    for provider, models in _MODELS_RAW.items()
}
