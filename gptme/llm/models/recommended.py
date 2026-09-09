"""Recommended (default) model per provider.

This is the single source of truth for what ``gptme -m <provider>`` resolves
to when no model is given, what onboarding/setup selects, and what the docs
advertise (``docs/evals.rst`` renders :func:`format_recommended_models` at
build time via ``gptme-util models recommended``), so updating a model here
updates all three.

Keep every entry present in :data:`gptme.llm.models.data.MODELS` for its
provider (enforced by tests) so the recommendation always has metadata.
"""

import json
from typing import Literal

from .types import PROVIDERS, Provider

# Recommended model per provider: the model we would pick for agentic work
# on that provider today. Providers without an entry (azure, nvidia, local,
# ...) require an explicit model name.
RECOMMENDED_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.6-sol",
    # GPT-6 Astra is flat-rate on ChatGPT Plus/Pro via Codex OAuth, so the
    # subscription default can be the frontier model without a cost tradeoff.
    "openai-subscription": "gpt-6-astra",
    # Official DeepSeek endpoint pinned with ``@deepseek`` for reliability;
    # see docs/evals.rst "Choosing an OpenRouter provider".
    "openrouter": "deepseek/deepseek-v4-flash-0731@deepseek",
    "gemini": "gemini-3.1-pro-preview",
    "xai": "grok-4.6",
    "grok-subscription": "grok-4.6",
    "gptme": "claude-sonnet-4-6",
    "deepseek": "deepseek-v4-flash",
    "groq": "llama-3.3-70b-versatile",
}

# Cheaper/faster model per provider used for summaries, titles, and other
# side tasks. Providers without an entry use the main model.
SUMMARY_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5-mini",
    "openrouter": "deepseek/deepseek-v4-flash-0731@deepseek",
    "gemini": "gemini-2.5-flash",
    "deepseek": "deepseek-v4-flash",
    "xai": "grok-4-1-fast",
}

RecommendedFormat = Literal["table", "rst", "markdown", "json"]


def get_recommended_model(provider: Provider) -> str:
    """Return the recommended model name (without provider prefix)."""
    try:
        return RECOMMENDED_MODELS[provider]
    except KeyError:
        raise ValueError(
            f"Provider '{provider}' requires specifying a model, "
            f"e.g. gptme -m {provider}/your-model-name"
        ) from None


def get_summary_model(provider: Provider) -> str | None:
    """Return a cheaper/faster summary model, or None to reuse the main model."""
    return SUMMARY_MODELS.get(provider)


def recommended_models_rows() -> list[dict[str, str]]:
    """Rows of (provider, recommended, summary) in PROVIDERS order."""
    rows = []
    for provider in PROVIDERS:
        if provider not in RECOMMENDED_MODELS:
            continue
        rows.append(
            {
                "provider": provider,
                "model": f"{provider}/{RECOMMENDED_MODELS[provider]}",
                "summary_model": (
                    f"{provider}/{SUMMARY_MODELS[provider]}"
                    if provider in SUMMARY_MODELS
                    else ""
                ),
            }
        )
    return rows


def _format_grid(rows: list[dict[str, str]], headers: dict[str, str]) -> str:
    widths = {
        key: max(len(title), *(len(row[key]) for row in rows))
        for key, title in headers.items()
    }
    sep = "+" + "+".join("-" * (widths[k] + 2) for k in headers) + "+"
    head_sep = "+" + "+".join("=" * (widths[k] + 2) for k in headers) + "+"

    def line(values: dict[str, str]) -> str:
        return "|" + "|".join(f" {values[k]:<{widths[k]}} " for k in headers) + "|"

    out = [sep, line(headers), head_sep]
    for row in rows:
        out.extend([line(row), sep])
    return "\n".join(out)


def format_recommended_models(fmt: RecommendedFormat = "table") -> str:
    """Render the recommended-model table in the given format."""
    rows = recommended_models_rows()
    headers = {
        "provider": "Provider",
        "model": "Recommended model",
        "summary_model": "Summary model",
    }
    if fmt == "json":
        return json.dumps(rows, indent=2)
    if fmt == "markdown":
        lines = [
            "| " + " | ".join(headers.values()) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
        ]
        lines += ["| " + " | ".join(row[k] for k in headers) + " |" for row in rows]
        return "\n".join(lines)
    if fmt == "rst":
        # Grid table with literal model ids so it can be included as reST.
        literal = [
            {k: (f"``{v}``" if k != "provider" and v else v) for k, v in row.items()}
            for row in rows
        ]
        return _format_grid(literal, headers)
    return _format_grid(rows, headers)
