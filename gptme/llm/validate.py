"""API key validation utilities for gptme."""

import logging
from enum import Enum

import requests

logger = logging.getLogger(__name__)


class ApiKeyValidationStatus(str, Enum):
    """Result of validating a provider API key.

    Distinct from a boolean because a network failure (provider unreachable)
    is not the same as the key being definitively rejected — a first-run save
    flow must not lock a user out of saving a key they know is good just
    because of a transient network blip.
    """

    VALID = "valid"
    INVALID = "invalid"  # provider explicitly rejected the key (401/403)
    UNREACHABLE = "unreachable"  # could not reach provider (timeout / connection / 5xx)
    UNSUPPORTED = (
        "unsupported"  # provider has no validation path (azure, local, custom)
    )


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

# Stable messages retained for callers of the legacy boolean API. New callers should
# use ApiKeyValidationStatus instead of parsing these strings.
VALIDATION_TIMEOUT_ERROR = "Request timed out. Please check your network connection."
VALIDATION_CONNECTION_ERROR = "Could not connect to the API. Please check your network."
VALIDATION_PROVIDER_ERROR_PREFIX = "Validation failed:"


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
        Tuple of (is_valid, message)
        - (True, "") if valid
        - (True, "warning message") if provider unreachable (warn but allow)
        - (False, "error description") if definitively invalid

    Note:
        Callers MUST check the second element and surface any non-empty warning
        to the user; a non-empty message with is_valid=True means the key was
        saved without being confirmed reachable, and the user should be notified.
        (All current built-in CLI callers — setup, doctor, onboard, account —
        already do this.)
    """
    status, message = validate_api_key_status(api_key, provider, timeout=timeout)
    # UNSUPPORTED providers (azure, local, custom) historically counted as
    # valid because there is no key to reject — preserve that for CLI callers.
    # UNREACHABLE also counts as "allow" — a transient network blip must not
    # lock a CLI user out of saving a key they know is good.
    return status in (
        ApiKeyValidationStatus.VALID,
        ApiKeyValidationStatus.UNSUPPORTED,
        ApiKeyValidationStatus.UNREACHABLE,
    ), message


def validate_api_key_status(
    api_key: str,
    provider: str,
    timeout: int = 10,
) -> tuple[ApiKeyValidationStatus, str]:
    """Validate an API key, returning a tri-state status distinct from a boolean.

    Unlike :func:`validate_api_key`, this distinguishes a definitively-rejected
    key (``INVALID``) from a provider that could not be reached
    (``UNREACHABLE``). Callers that gate a first-run save on validation should
    block on ``INVALID`` but only warn on ``UNREACHABLE`` so a transient network
    blip never locks the user out of saving a key they know is good.
    """
    try:
        if provider == "openai":
            is_valid, message = _validate_openai(api_key, timeout)
        elif provider == "anthropic":
            is_valid, message = _validate_anthropic(api_key, timeout)
        elif provider == "openrouter":
            is_valid, message = _validate_openrouter(api_key, timeout)
        elif provider == "requesty":
            is_valid, message = _validate_openai_compatible(
                api_key, timeout, "https://router.requesty.ai/v1"
            )
        elif provider == "moonshot":
            is_valid, message = _validate_openai_compatible(
                api_key, timeout, "https://api.moonshot.ai/v1"
            )
        elif provider in ("google", "gemini"):
            is_valid, message = _validate_google(api_key, timeout)
        elif provider == "groq":
            is_valid, message = _validate_groq(api_key, timeout)
        elif provider == "deepseek":
            is_valid, message = _validate_deepseek(api_key, timeout)
        elif provider == "xai":
            is_valid, message = _validate_xai(api_key, timeout)
        elif provider == "azure":
            # Azure requires endpoint configuration, skip live validation
            logger.info("Azure API key validation skipped (requires endpoint config)")
            return (
                ApiKeyValidationStatus.UNSUPPORTED,
                "Key accepted without live validation — Azure requires endpoint configuration to validate",
            )
        elif provider == "nvidia":
            # NVIDIA validation would need an org-specific endpoint, skip
            logger.info("NVIDIA API key validation skipped (requires org endpoint)")
            return (
                ApiKeyValidationStatus.UNSUPPORTED,
                "Key accepted without live validation — NVIDIA keys are checked on first use",
            )
        elif provider == "local":
            # Local models typically use a placeholder key or none at all
            logger.info("Local provider doesn't require API key validation")
            return ApiKeyValidationStatus.UNSUPPORTED, ""
        else:
            # Unknown or custom provider, skip validation
            logger.info(f"No validation available for provider: {provider}")
            return ApiKeyValidationStatus.UNSUPPORTED, ""
        if is_valid:
            return ApiKeyValidationStatus.VALID, message
        return ApiKeyValidationStatus.INVALID, message
    except requests.exceptions.Timeout:
        return ApiKeyValidationStatus.UNREACHABLE, VALIDATION_TIMEOUT_ERROR
    except requests.exceptions.ConnectionError:
        return ApiKeyValidationStatus.UNREACHABLE, VALIDATION_CONNECTION_ERROR
    except requests.exceptions.HTTPError as e:
        # 408/5xx from the provider — server is reachable but broken; not a key rejection.
        status_code = e.response.status_code if e.response is not None else "unknown"
        message = (
            f"{VALIDATION_PROVIDER_ERROR_PREFIX} Provider returned server error "
            f"{status_code}. Please try again later."
        )
        return ApiKeyValidationStatus.UNREACHABLE, message
    except requests.exceptions.RequestException as e:
        logger.exception(f"Unexpected error validating {provider} API key")
        return (
            ApiKeyValidationStatus.UNREACHABLE,
            f"{VALIDATION_PROVIDER_ERROR_PREFIX} {e}",
        )


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
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
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
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
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
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
    return False, f"API returned status {response.status_code}"


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
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
    return False, f"API returned status {response.status_code}"


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
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
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
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
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
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
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
    if response.status_code == 401:
        return False, "Invalid API key. Please check your key and try again."
    if response.status_code == 429:
        return True, ""  # Rate limited but key is valid
    if response.status_code == 408 or response.status_code >= 500:
        response.raise_for_status()
    return False, f"API returned status {response.status_code}"
