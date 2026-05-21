"""
Tavily search backend for the browser tool.
"""

import logging

logger = logging.getLogger(__name__)


def search_tavily(query: str) -> str:
    """Search using Tavily API."""
    try:
        try:
            from tavily import TavilyClient
        except ImportError:
            return (
                "Error: tavily-python package not installed. "
                "Install with: pip install tavily-python"
            )

        from ..config import get_config

        cfg = get_config()
        api_key = cfg.get_env("TAVILY_API_KEY")
        if not api_key:
            return (
                "Error: Tavily search not available. Set TAVILY_API_KEY "
                "environment variable or add it to ~/.config/gptme/config.toml"
            )

        client = TavilyClient(api_key=api_key)
        response = client.search(query=query, max_results=5, search_depth="basic")

        results = response.get("results", [])
        if not results:
            return "Error: No results from Tavily API"

        parts: list[str] = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")
            parts.append(f"### {title}\n{url}\n\n{content}")

        return "\n\n".join(parts)

    except Exception as e:
        return f"Error searching with Tavily: {e}"


def has_tavily_key() -> bool:
    """Check if Tavily API key is available."""
    from ..config import get_config

    cfg = get_config()
    return bool(cfg.get_env("TAVILY_API_KEY"))
