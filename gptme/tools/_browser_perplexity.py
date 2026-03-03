"""
Perplexity search backend for the browser tool.
"""

import logging

from ..llm.llm_openai import OPENROUTER_APP_HEADERS

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a helpful AI assistant.

Rules:
1. Provide only the final answer. It is important that you do not include any explanation on the steps below.
2. Do not show the intermediate steps information.

Steps:
1. Decide if the answer should be a brief sentence or a list of suggestions.
2. If it is a list of suggestions, first, write a brief and natural introduction based on the original query.
3. Followed by a list of suggestions, each suggestion should be split by two newlines.
""".strip()


def search_perplexity(query: str) -> str:
    """Search using Perplexity AI API."""
    try:
        # Try to import OpenAI
        try:
            from openai import OpenAI
        except ImportError:
            return (
                "Error: OpenAI package not installed. Install with: pip install openai"
            )

        from ..config import get_config

        cfg = get_config()

        # Get API key - try Perplexity first, then OpenRouter
        api_key = cfg.get_env("PERPLEXITY_API_KEY")
        use_openrouter = False

        if not api_key:
            api_key = cfg.get_env("OPENROUTER_API_KEY")
            if api_key:
                use_openrouter = True

        if not api_key:
            return "Error: Perplexity search not available. Set PERPLEXITY_API_KEY or OPENROUTER_API_KEY environment variable or add it to ~/.config/gptme/config.toml"

        # Create client and search
        headers: dict[str, str] = {}
        if use_openrouter:
            client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            model = "perplexity/sonar-pro"
            headers |= OPENROUTER_APP_HEADERS
        else:
            client = OpenAI(
                api_key=api_key,
                base_url="https://api.perplexity.ai",
            )
            model = "sonar-pro"

        response = client.chat.completions.create(
            model=model,
            extra_headers=headers,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": query,
                },
            ],
        )

        msg = response.choices[0].message
        if not msg.content:
            return "Error: No response from Perplexity API"

        return msg.content

    except Exception as e:
        return f"Error searching with Perplexity: {e}"


def has_perplexity_key() -> bool:
    """Check if Perplexity or OpenRouter API key is available."""
    from ..config import get_config

    cfg = get_config()
    return bool(cfg.get_env("PERPLEXITY_API_KEY") or cfg.get_env("OPENROUTER_API_KEY"))
