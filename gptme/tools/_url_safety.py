"""Shared URL safety checks for browser backends.

Used by the lynx backend and the PDF/requests paths in the Playwright browser
tool so file:// and credentialed URLs never reach a subprocess or HTTP client.

See: https://github.com/gptme/gptme/issues/1021
     https://github.com/gptme/gptme/pull/3663
"""

from urllib.parse import urlparse

_MAX_INPUT_LENGTH = 2048


def _validate_url_scheme(url: str) -> None:
    """Validate that a URL is safe to fetch over HTTP(S).

    Security: Prevents file:// protocol from reading local files.
    See: https://github.com/gptme/gptme/issues/1021
    """
    if not url or len(url) > _MAX_INPUT_LENGTH:
        raise ValueError(
            f"URL must be non-empty and no longer than {_MAX_INPUT_LENGTH} characters."
        )

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("Invalid URL") from exc

    allowed_schemes = {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError(
            f"URL scheme '{parsed.scheme}' not allowed. "
            f"Only {allowed_schemes} are permitted for security reasons."
        )
    if not hostname:
        raise ValueError("URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not include embedded credentials.")
