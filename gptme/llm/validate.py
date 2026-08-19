"""API key validation utilities for gptme."""

import logging

import requests

logger = logging.getLogger(__name__)

# Provider documentation URLs
PROVIDER_DOCS: dict[str, str] = {
    "openai": "https://platform.openai.com/account/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openrouter": "https://openrouter.ai/settings/keys",
    "gemini": "https://aistudio.google.com/app/apikey",
    "google": "https://aistudio.google.com/app/apikey",  # alias for gemini
    "groq": "https://console.groq.com/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "xai": "https://console.x.ai/",
    "azure": "https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/CognitiveServicesHub",
    "nvidia": "https://build.nvidia.com/",
    "requesty": "https://app.requesty.ai/api-keys",
    "moonshot": "https://platform.moonshot.ai/console/api-keys",
    "local": "https://gptme.org/docs/providers.html#local-models",
    "openai-subscription": "https://gptme.org/docs/providers.html#openai-subscription",
    "grok-subscription": "https://gptme.org/docs/providers.html#grok-subscription",
}

# Providers that use OAuth instead of API keys
OAUTH_PROVIDERS: set[str] = {"openai-subscription", "grok-subscription"}

# Stable messages used by callers to distinguish provider availability failures from
# credential failures without parsing provider-specific responses.
VALIDATION_TIMEOUT_ERROR = "Request timed out. Please check your network connection."
VALIDATION_CONNECTION_ERROR = "Could not connect to the API. Please check your network."
VALIDATION_PROVIDER_ERROR_PREFIX = "Validation failed:"


def _http_status_error(status_code: int) -> tuple[bool, str]:
    """Map an unexpected HTTP status to a validation error tuple.

    5xx responses mean the provider is down, not that the key is bad, so they
    use VALIDATION_PROVIDER_ERROR_PREFIX so api_v2 can return 502 instead of 422.
    """
    if status_code >= 500:
        return (
            False,
            f"{VALIDATION_PROVIDER_ERROR_PREFIX} Provider unavailable (HTTP {status_code})",
        )
    return False, f"API returned status {status_code}"


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
        if provider == "anthropic":
            return _validate_anthropic(api_key, timeout)
        if provider == "openrouter":
            return _validate_openrouter(api_key, timeout)
        if provider == "requesty":
            return _validate_openai_compatible(
                api_key, timeout, "https://router.requesty.ai/v1"
            )
        if provider == "moonshot":
            return _validate_openai_compatible(
                api_key, timeout, "https://api.moonshot.ai/v1"
            )
        if provider in ("google", "gemini"):
            return _validate_google(api_key, timeout)
        if provider == "groq":
            return _validate_groq(api_key, timeout)
        if provider == "deepseek":
            return _validate_deepseek(api_key, timeout)
        if provider == "xai":
            return _validate_xai(api_key, timeout)
        if provider == "azure":
            # Azure requires endpoint configuration, skip live validation
            logger.info("Azure API key validation skipped (requires endpoint config)")
            return (
                True,
                "Key accepted without live validation — Azure requires endpoint configuration to validate",
            )
        if provider == "nvidia":
            # NVIDIA validation would need an org-specific endpoint, skip
            logger.info("NVIDIA API key validation skipped (requires org endpoint)")
            return (
                True,
                "Key accepted without live validation — NVIDIA keys are checked on first use",
            )
        if provider == "local":
            # Local models typically use a placeholder key or none at all
            logger.info("Local provider doesn't require API key validation")
            return True, ""
        # Unknown or custom provider, skip validation
        logger.info(f"No validation available for provider: {provider}")
        return True, ""
    except requests.exceptions.Timeout:
        return False, VALIDATION_TIMEOUT_ERROR
    except requests.exceptions.ConnectionError:
        return False, VALIDATION_CONNECTION_ERROR
    except requests.exceptions.RequestException as e:
        logger.exception(f"Unexpected error validating {provider} API key")
        return False, f"{VALIDATION_PROVIDER_ERROR_PREFIX} {e}"


def _validate_openai(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate OpenAI API key by listing models."""
    response = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        # Rate limited but key is valid
        return True, ""
    return _http_status_error(response.status_code)


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
            "model": "claude-haiku-4-5",
            "max_tokens": 1,
            "messages": [],  # Empty messages will fail validation but key is checked first
        },
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 400:
        # Bad request means key is valid but request format was wrong (expected)
        try:
            error_data = response.json()
        except ValueError:
            return False, "Invalid API key. Please check your key and try again."
        error = error_data.get("error", {})
        error_type = error.get("type", "").lower()
        error_msg = error.get("message", "").lower()
        # Check permission_error BEFORE auth substrings: the key is valid even if
        # the message happens to contain "authentication" (e.g. "Authentication
        # required to access model"). If auth-substrings were checked first, a
        # permission_error with such a message would be misclassified as invalid.
        if "permission_error" in error_type:
            # Key is valid but lacks access to the probe model; the key will work
            # for models the account can actually access.
            return (
                True,
                "API key valid but lacks access to the probe model. It may work for other models.",
            )
        if (
            "authentication" in error_msg
            or "invalid api key" in error_msg
            or "invalid_api_key" in error_type
            or "authentication_error" in error_type
        ):
            return False, "Invalid API key. Please check your key and try again."
        if "usage limits" in error_msg:
            # Key is valid but account has hit its usage quota
            raw_msg = error_data.get("error", {}).get("message", "")
            return True, f"API quota exhausted — {raw_msg}"
        return True, ""  # Key is valid, request format was just wrong
    if response.status_code == 429:
        # Rate limited but key is valid
        return True, ""
    return _http_status_error(response.status_code)


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
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    return _http_status_error(response.status_code)


def _validate_openai_compatible(
    api_key: str, timeout: int, base_url: str
) -> tuple[bool, str]:
    """Validate an OpenAI-compatible provider key by listing models."""
    response = requests.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 403:
        return False, "API key forbidden. It may lack required permissions."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    return _http_status_error(response.status_code)


def _validate_google(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate Google AI (Gemini) API key by listing models."""
    response = requests.get(
        f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    if response.status_code == 400:
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        if error.get("status") == "INVALID_ARGUMENT":
            return False, "Invalid API key. Please check your key and try again."
        return False, error.get("message", "Unknown error")
    if response.status_code == 403:
        return False, "API key forbidden. It may lack required permissions."
    if response.status_code == 429:
        return True, ""
    return _http_status_error(response.status_code)


def _validate_groq(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate Groq API key by listing models."""
    response = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    return _http_status_error(response.status_code)


def _validate_deepseek(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate DeepSeek API key by listing models."""
    response = requests.get(
        "https://api.deepseek.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    return _http_status_error(response.status_code)


def _validate_xai(api_key: str, timeout: int) -> tuple[bool, str]:
    """Validate xAI (Grok) API key by listing models."""
    response = requests.get(
        "https://api.x.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if response.status_code == 200:
        return True, ""
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    return _http_status_error(response.status_code)
