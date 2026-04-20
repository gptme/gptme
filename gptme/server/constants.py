"""Server-wide constants and configuration defaults."""

# Default model to use when no model is configured
# This is used as a fallback in cli.py and shown as an example in api_v2_sessions.py
DEFAULT_FALLBACK_MODEL = "anthropic/claude-sonnet-4-6"

# Per-provider fallback models used when no MODEL/--model is configured but some
# provider credentials exist. Keeps the fallback provider-aware so the server
# can start with whatever the user has configured, not only Anthropic.
#
# Values are intentionally cheap/fast defaults — the goal is "server starts"
# for first-run UX, not "optimal model choice." Users set MODEL explicitly for
# real use.
PROVIDER_FALLBACK_MODELS: dict[str, str] = {
    "anthropic": DEFAULT_FALLBACK_MODEL,
    "openai": "openai/gpt-4o-mini",
    "openrouter": "openrouter/anthropic/claude-haiku-4-5",
    "gemini": "gemini/gemini-2.0-flash",
    "groq": "groq/llama-3.1-8b-instant",
    "xai": "xai/grok-3-mini",
    "deepseek": "deepseek/deepseek-chat",
    "openai-subscription": "openai-subscription/gpt-5.2",
}
