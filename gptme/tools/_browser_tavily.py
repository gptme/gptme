"""Tavily search backend for the browser tool."""

from typing import Any

import requests

from ..config import get_config

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_CLIENT_NAME = "open-monitor/gptme/gptme"
TAVILY_CLIENT_SOURCE = "open-monitor"
MAX_RESULTS = 5
MAX_CONTENT_LENGTH = 2000


def search_tavily(query: str) -> str:
    """Search using the Tavily Search API."""
    api_key = get_config().get_env("TAVILY_API_KEY")
    if not api_key:
        return (
            "Error: Tavily search not available. Set TAVILY_API_KEY environment "
            "variable or add it to ~/.config/gptme/config.toml"
        )

    try:
        response = requests.post(
            TAVILY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Client-Name": TAVILY_CLIENT_NAME,
                "X-Client-Source": TAVILY_CLIENT_SOURCE,
            },
            json={
                "query": query,
                "search_depth": "basic",
                "topic": "general",
                "max_results": MAX_RESULTS,
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            return "Error: Tavily returned invalid JSON"
        if not isinstance(data, dict):
            return "Error: Tavily returned an invalid search response"

        results = data.get("results")
        if not isinstance(results, list):
            return "Error: Tavily returned an invalid search response"

        formatted_results = [
            formatted
            for index, result in enumerate(results, start=1)
            if isinstance(result, dict)
            if (formatted := _format_result(index, result))
        ]
        if not formatted_results:
            return "Error: Tavily returned no valid search results"

        return "\n\n".join(formatted_results)
    except Exception as exc:
        return f"Error searching with Tavily: {exc}"


def _format_result(index: int, result: dict[str, Any]) -> str:
    """Format one Tavily result for the browser tool."""
    title = _optional_text(result, "title")
    url = _optional_text(result, "url")
    if not title or not url:
        return ""

    content = _optional_text(result, "content") or _optional_text(result, "raw_content")
    if len(content) > MAX_CONTENT_LENGTH:
        content = content[:MAX_CONTENT_LENGTH].rstrip() + "..."

    lines = [f"{index}. {title} ({url})"]
    if content:
        lines.append(f"   {content}")

    metadata = []
    if (score := result.get("score")) is not None:
        metadata.append(f"Relevance: {score}")
    if published_date := _optional_text(result, "published_date"):
        metadata.append(f"Published: {published_date}")
    if favicon := _optional_text(result, "favicon"):
        metadata.append(f"Favicon: {favicon}")
    if metadata:
        lines.append(f"   {' | '.join(metadata)}")
    return "\n".join(lines)


def _optional_text(result: dict[str, Any], key: str) -> str:
    """Return a non-empty text result field."""
    value = result.get(key)
    return value.strip() if isinstance(value, str) else ""


def has_tavily_key() -> bool:
    """Check if a Tavily API key is available."""
    return bool(get_config().get_env("TAVILY_API_KEY"))
