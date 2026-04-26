# Shared constants for the llm package.

# Minimum tokens reserved for the actual response content when a thinking budget
# is active. Prevents near-zero response budgets when the caller supplies a
# max_tokens value that is only slightly above the thinking budget.
_MIN_RESPONSE_TOKENS = 256

# Shows in rankings on openrouter.ai. Defined here so importers (e.g. the
# Perplexity browser backend) don't pull in the openai SDK transitively.
OPENROUTER_APP_HEADERS = {
    "HTTP-Referer": "https://github.com/gptme/gptme",
    "X-Title": "gptme",
}
