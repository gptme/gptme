from unittest.mock import MagicMock, patch

from gptme.tools._browser_tavily import search_tavily


@patch("gptme.tools._browser_tavily.get_config")
@patch("gptme.tools._browser_tavily.requests.post")
def test_search_tavily_formats_results(mock_post, mock_get_config):
    mock_get_config.return_value.get_env.return_value = "test-key"
    response = MagicMock()
    response.json.return_value = {
        "results": [
            {
                "title": "Tavily Docs",
                "url": "https://docs.tavily.com",
                "content": "Search API documentation",
                "score": 0.91,
                "published_date": "2026-08-01",
                "favicon": "https://docs.tavily.com/favicon.ico",
            }
        ]
    }
    mock_post.return_value = response

    result = search_tavily("Tavily API")

    assert "1. Tavily Docs (https://docs.tavily.com)" in result
    assert "Search API documentation" in result
    assert "Relevance: 0.91" in result
    assert "Published: 2026-08-01" in result
    assert "Favicon: https://docs.tavily.com/favicon.ico" in result
    mock_post.assert_called_once_with(
        "https://api.tavily.com/search",
        headers={
            "Authorization": "Bearer test-key",
            "Content-Type": "application/json",
            "X-Client-Name": "open-monitor/gptme/gptme",
            "X-Client-Source": "open-monitor",
        },
        json={
            "query": "Tavily API",
            "search_depth": "basic",
            "topic": "general",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=30,
    )


@patch("gptme.tools._browser_tavily.get_config")
def test_search_tavily_requires_key(mock_get_config):
    mock_get_config.return_value.get_env.return_value = None

    result = search_tavily("Tavily API")

    assert result.startswith("Error:")
    assert "TAVILY_API_KEY" in result


@patch("gptme.tools._browser_tavily.get_config")
@patch("gptme.tools._browser_tavily.requests.post")
def test_search_tavily_rejects_invalid_response(mock_post, mock_get_config):
    mock_get_config.return_value.get_env.return_value = "test-key"
    response = MagicMock()
    response.json.return_value = {"results": [{"title": "Missing URL"}]}
    mock_post.return_value = response

    result = search_tavily("Tavily API")

    assert result == "Error: Tavily returned no valid search results"


@patch("gptme.tools._browser_tavily.get_config")
@patch("gptme.tools._browser_tavily.requests.post")
def test_search_tavily_rejects_invalid_json(mock_post, mock_get_config):
    mock_get_config.return_value.get_env.return_value = "test-key"
    response = MagicMock()
    response.json.side_effect = ValueError("invalid json")
    mock_post.return_value = response

    result = search_tavily("Tavily API")

    assert result == "Error: Tavily returned invalid JSON"


@patch("gptme.tools._browser_tavily.get_config")
@patch("gptme.tools._browser_tavily.requests.post")
def test_search_tavily_returns_api_error(mock_post, mock_get_config):
    mock_get_config.return_value.get_env.return_value = "test-key"
    mock_post.side_effect = RuntimeError("request failed")

    result = search_tavily("Tavily API")

    assert result == "Error searching with Tavily: request failed"
