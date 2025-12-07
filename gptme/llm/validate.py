"""API key validation utilities for gptme."""

import logging

import requests

logger = logging.getLogger(__name__)

# Provider documentation URLs
PROVIDER_DOCS: dict[str, str] = {
    "openai": "https://platform.openai.com/account/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openrouter": "https://openrouter.ai/settings/keys",
    "google": "https://aistudio.google.com/app/apikey",
    "groq": "https://console.groq.com/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "xai": "https://console.x.ai/",
    "local": "https://gptme.org/docs/providers.html#local-models",
}


def validate_api_key(
    api_key: str,
    provider: str,
    timeout: int = 10,
) -> tuple[bool, str]:
    """
    Validate an API key by making a cheap test request to the provider.

    Args:
        api_key: The API key to validate
        provider: The provider name
        timeout: Request timeout in seconds

    Returns:
        Tuple of (is_valid, error_message)
        - (True, "") if valid
        - (False, "error description") if invalid
    """
    try:
        if provider == "openai":
            return _validate_openai(api_key, timeout)
        elif provider == "anthropic":
            return _validate_anthropic(api_key, timeout)
        elif provider == "openrouter":
            return _validate_openrouter(api_key, timeout)
        elif provider in ("google", "gemini"):
            return _validate_google(api_key, timeout)
        elif provider == "groq":
            return _validate_groq(api_key, timeout)
        elif provider == "deepseek":
            return _validate_deepseek(api_key, timeout)
        elif provider == "xai":
            return _validate_xai(api_key, timeout)
        elif provider == "azure":
            # Azure requires endpoint configuration, skip validation
            logger.info("Azure API key validation skipped (requires endpoint config)")
            return True, ""
        elif provider in ("nvidia", "local"):
            # Local models don't need API key validation
            logger.info(f"{provider} provider doesn't require API key validation")
            return True, ""
        else:
            # Unknown or custom provider, skip validation
            logger.info(f"No validation available for provider: {provider}")
            return True, ""
    except requests.exceptions.Timeout:
        return False, "Request timed out. Please check your network connection."
    except requests.exceptions.ConnectionError:
        return False, "Could not connect to the API. Please check your network."
    except Exception as e:
        logger.exception(f"Unexpected error validating {provider} API key")
        return False, f"Validation failed: {e}"


def _validate_openai(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate OpenAI API key by listing models."""
    response = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    elif response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    elif response.status_code == 429:
        # Rate limited but key is valid
        return True, ""
    else:
        return False, f"API returned status {response.status_code}"


def _validate_anthropic(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate Anthropic API key by checking the messages endpoint."""
    # Make a minimal request that will fail validation but confirm key works
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-3-haiku-20240307",
            "max_tokens": 1,
            "messages": [],  # Empty messages will fail validation but key is checked first
        },
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    elif response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    elif response.status_code == 400:
        # Bad request means key is valid but request format was wrong (expected)
        try:
            error_data = response.json()
        except Exception:
            return False, "Invalid API key. Please check your key and try again."
        if "authentication" in error_data.get("error", {}).get("message", "").lower():
            return False, "Invalid API key. Please check your key and try again."
        return True, ""  # Key is valid, request format was just wrong
    elif response.status_code == 429:
        # Rate limited but key is valid
        return True, ""
    else:
        return False, f"API returned status {response.status_code}"


def _validate_openrouter(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate OpenRouter API key by listing models."""
    response = requests.get(
        "https://openrouter.ai/api/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/gptme/gptme",
            "X-Title": "gptme",
        },
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    elif response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    elif response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    else:
        return False, f"API returned status {response.status_code}"


def _validate_google(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate Google AI (Gemini) API key by listing models."""
    response = requests.get(
        f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    elif response.status_code == 400:
        try:
            error = response.json().get("error", {})
        except Exception:
            error = {}
        if error.get("status") == "INVALID_ARGUMENT":
            return False, "Invalid API key. Please check your key and try again."
        return False, error.get("message", "Unknown error")
    elif response.status_code == 403:
        return False, "API key forbidden. It may lack required permissions."
    else:
        return False, f"API returned status {response.status_code}"


def _validate_groq(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate Groq API key by listing models."""
    response = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    elif response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    elif response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    else:
        return False, f"API returned status {response.status_code}"


def _validate_deepseek(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate DeepSeek API key by listing models."""
    response = requests.get(
        "https://api.deepseek.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    elif response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    elif response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    else:
        return False, f"API returned status {response.status_code}"


def _validate_xai(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate xAI (Grok) API key by listing models."""
    response = requests.get(
        "https://api.x.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    elif response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    elif response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    else:
        return False, f"API returned status {response.status_code}"
