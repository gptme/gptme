"""
Perplexity search backend for the browser tool.
"""

import logging
import os
from pathlib import Path

import tomlkit

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

        # Get API key - try Perplexity first, then OpenRouter
        api_key = os.getenv("PERPLEXITY_API_KEY")
        use_openrouter = False

        if not api_key:
            # Try config file
            config_path = Path.home() / ".config" / "gptme" / "config.toml"
            config_env = {}
            if config_path.exists():
                with open(config_path) as f:
                    config = tomlkit.load(f)
                    config_env = config.get("env", {})

            # Try Perplexity key from config
            api_key = config_env.get("PERPLEXITY_API_KEY")

            if not api_key:
                # Try OpenRouter as fallback
                api_key = os.getenv("OPENROUTER_API_KEY") or config_env.get(
                    "OPENROUTER_API_KEY"
                )
                if api_key:
                    use_openrouter = True

        if not api_key:
            return "Error: No API key found. Set PERPLEXITY_API_KEY or OPENROUTER_API_KEY environment variable or add it to ~/.config/gptme/config.toml"

        # Create client and search
        if use_openrouter:
            client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
            model = "perplexity/sonar-pro"
        else:
            client = OpenAI(
                api_key=api_key,
                base_url="https://api.perplexity.ai",
            )
            model = "sonar-pro"

        response = client.chat.completions.create(
            model=model,
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
        return f"Error searching with Perplexity: {str(e)}"


def has_perplexity_key() -> bool:
    """Check if Perplexity or OpenRouter API key is available."""
    # Check for Perplexity key
    if os.getenv("PERPLEXITY_API_KEY"):
        return True

    # Check for OpenRouter key (can be used for Perplexity search)
    if os.getenv("OPENROUTER_API_KEY"):
        return True

    # Try config file
    config_path = Path.home() / ".config" / "gptme" / "config.toml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = tomlkit.load(f)
                env_config = config.get("env", {})
                return bool(
                    env_config.get("PERPLEXITY_API_KEY")
                    or env_config.get("OPENROUTER_API_KEY")
                )
        except Exception:
            pass

    return False
