import hashlib
import logging
import os
import threading
import typing

if typing.TYPE_CHECKING:
    import tiktoken  # fmt: skip

    from ..message import Message  # fmt: skip


# Global cache mapping hashes to token counts
_token_cache: dict[tuple[str, str], int] = {}

# Cache for successfully loaded tokenizers (keyed by model name).
# Unlike @lru_cache, failed loads (timeout / offline) are NOT stored here,
# so a future call can retry (e.g. after network recovery or TIKTOKEN_CACHE_DIR is populated).
_tokenizer_cache: dict[str, "tiktoken.Encoding"] = {}

_warned_models: set[str] = set()

logger = logging.getLogger(__name__)


def _parse_tiktoken_timeout() -> float:
    """Parse GPTME_TIKTOKEN_TIMEOUT from the environment, falling back to 5.0."""
    raw = os.environ.get("GPTME_TIKTOKEN_TIMEOUT", "5.0")
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            f"Invalid GPTME_TIKTOKEN_TIMEOUT value {raw!r}; using default 5.0 seconds."
        )
        return 5.0


# Timeout (seconds) for tiktoken encoding fetches. On first use tiktoken may
# need to download BPE data from the internet (~1-2 MB per encoding). Users
# on airgapped systems or pointing gptme at a local endpoint (Ollama, vLLM,
# LiteLLM, …) would otherwise block here indefinitely on a TCP black-hole.
#
# Set GPTME_TIKTOKEN_TIMEOUT=0 to skip tiktoken entirely (always use the
# ~4 chars/token approximation). Set a larger value if the download
# consistently times out on a slow internet connection.
_TIKTOKEN_TIMEOUT = _parse_tiktoken_timeout()


def _load_encoding(name: str) -> "tiktoken.Encoding":
    """Load a tiktoken encoding (may trigger a network download on first use)."""
    import tiktoken  # fmt: skip

    return tiktoken.get_encoding(name)


class _GetTokenizer:
    """Callable that loads tokenizers with timeout, caching only successful results.

    Exposes ``cache_clear()`` for test compatibility with the previous
    ``@lru_cache``-based implementation.
    """

    def __call__(self, model: str) -> "tiktoken.Encoding | None":
        """Get the tokenizer for a given model, with caching and fallbacks.

        Returns None if tiktoken is unavailable or encodings can't be loaded
        (e.g. offline/airgapped environments). Callers should fall back to
        character-based approximation when None is returned.

        Successful loads are cached; failed or timed-out loads are NOT cached,
        allowing retries after network recovery or TIKTOKEN_CACHE_DIR is populated.

        The first call for a given encoding may trigger a network download of
        BPE data from OpenAI's CDN. To avoid blocking indefinitely on local /
        airgapped setups, the download is wrapped in a timeout controlled by
        the GPTME_TIKTOKEN_TIMEOUT env var (default 5 s). Set it to 0 to
        always use the char-based approximation without attempting a download.
        Pre-cache the encodings by running once with internet access, or set
        TIKTOKEN_CACHE_DIR to a directory containing the pre-downloaded files.
        """
        if model in _tokenizer_cache:
            return _tokenizer_cache[model]

        try:
            import tiktoken  # fmt: skip
        except ImportError:
            logger.warning(
                "tiktoken not installed. Token counts will use character-based approximation."
            )
            return None

        # Allow users to skip tiktoken entirely (useful for local-only setups).
        if _TIKTOKEN_TIMEOUT <= 0:
            logger.debug(
                "GPTME_TIKTOKEN_TIMEOUT<=0: skipping tiktoken, using char-based approximation."
            )
            return None

        try:
            # Determine the encoding name without loading BPE data (instant).
            if "gpt-4o" in model:
                encoding_name = "o200k_base"
            else:
                # Strip known provider prefixes so tiktoken.encoding_for_model can
                # match the bare model name (e.g. "openai/o1" → "o1").
                _provider_prefixes = [
                    "openai/",
                    "anthropic/",
                    "google/",
                    "azure/",
                    "vertex/",
                ]
                bare_model = model
                for prefix in _provider_prefixes:
                    if model.startswith(prefix):
                        bare_model = model[len(prefix) :]
                        break

                try:
                    # MODEL_TO_ENCODING is a static dict — no network needed.
                    _enc: str | None = tiktoken.model.MODEL_TO_ENCODING.get(bare_model)
                    if _enc is None:
                        # Check prefix table (e.g. "gpt-3.5-turbo-*")
                        _enc = next(
                            (
                                enc
                                for prefix, enc in tiktoken.model.MODEL_PREFIX_TO_ENCODING.items()
                                if bare_model.startswith(prefix)
                            ),
                            None,
                        )
                    if _enc is None:
                        global _warned_models
                        if bare_model not in _warned_models:
                            logger.debug(
                                f"No tokenizer for '{bare_model}'. Using tiktoken cl100k_base."
                                " Use results only as estimates."
                            )
                            _warned_models |= {bare_model}
                        encoding_name = "cl100k_base"
                    else:
                        encoding_name = _enc
                except AttributeError:
                    # Older tiktoken versions may not expose MODEL_TO_ENCODING.
                    encoding_name = "cl100k_base"

            # Load encoding with a timeout so we don't hang on airgapped systems.
            # Use a daemon thread so a timed-out fetch doesn't keep a short-lived
            # process alive — non-daemon threads block process exit until they finish,
            # which can delay CLI exit by the full TCP timeout.
            result_holder: list[tiktoken.Encoding | Exception | None] = [None]

            def _do_load() -> None:
                try:
                    result_holder[0] = _load_encoding(encoding_name)
                except Exception as exc:
                    result_holder[0] = exc

            t = threading.Thread(target=_do_load, daemon=True, name="tiktoken-fetch")
            t.start()
            t.join(timeout=_TIKTOKEN_TIMEOUT)

            if t.is_alive():
                # Still fetching after timeout; return None without caching so that
                # a future call can retry (e.g. after pre-caching the encodings).
                logger.warning(
                    f"tiktoken encoding '{encoding_name}' fetch timed out after"
                    f" {_TIKTOKEN_TIMEOUT:.1f}s. Using character-based approximation"
                    " (~4 chars/token). For offline/local-model use: pre-cache by"
                    " running once with internet access, or set TIKTOKEN_CACHE_DIR"
                    " to a directory with the encoding files. Set"
                    " GPTME_TIKTOKEN_TIMEOUT=0 to always use the approximation."
                )
                return None

            enc_or_exc = result_holder[0]
            if isinstance(enc_or_exc, Exception):
                raise enc_or_exc

            if enc_or_exc is not None:
                _tokenizer_cache[model] = enc_or_exc
            return enc_or_exc

        except Exception as e:
            logger.warning(
                f"Failed to load tiktoken encoding: {e}. "
                "Token counts will use character-based approximation (~4 chars/token)."
            )
            return None

    def cache_clear(self) -> None:
        """Clear the tokenizer cache (for testing or after environment changes)."""
        _tokenizer_cache.clear()


get_tokenizer = _GetTokenizer()


def _hash_content(content: str) -> str:
    """Create a hash of the content"""
    return hashlib.sha256(content.encode()).hexdigest()


def len_tokens(content: "str | Message | list[Message]", model: str) -> int:
    """Get the number of tokens in a string, message, or list of messages.

    Uses efficient caching with content hashing to minimize memory usage while
    maintaining fast repeated calculations, which is especially important for
    conversations with many messages.
    """
    from ..message import Message  # fmt: skip

    if isinstance(content, list):
        return sum(len_tokens(msg, model) for msg in content)
    if isinstance(content, Message):
        content = content.content

    assert isinstance(content, str), content
    # Check cache using hash
    content_hash = _hash_content(content)
    cache_key = (content_hash, model)
    if cache_key in _token_cache:
        return _token_cache[cache_key]

    # Calculate and cache
    tokenizer = get_tokenizer(model)
    if tokenizer is not None:
        count = len(tokenizer.encode(content, disallowed_special=[]))
        # Only cache real token counts — approximations are not cached so that
        # accurate counts are used if the tokenizer later becomes available
        # (e.g. after network recovery in an offline environment).
        _token_cache[cache_key] = count
        # Limit cache size by removing oldest entries if needed
        if len(_token_cache) > 1000:
            # Remove first item (oldest in insertion order)
            _token_cache.pop(next(iter(_token_cache)))
    else:
        # Approximate: ~4 characters per token for English text
        count = len(content) // 4

    return count
