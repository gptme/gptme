import hashlib
import logging
import typing
from functools import lru_cache

if typing.TYPE_CHECKING:
    import tiktoken  # fmt: skip

    from ..message import Message  # fmt: skip


# Global cache mapping hashes to token counts
_token_cache: dict[tuple[str, str], int] = {}

_warned_models = set()

logger = logging.getLogger(__name__)


@lru_cache
def get_tokenizer(model: str) -> "tiktoken.Encoding":
    """Get the tokenizer for a given model, with caching and fallbacks."""
    import tiktoken  # fmt: skip

    if "gpt-4o" in model:
        return tiktoken.get_encoding("o200k_base")

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        global _warned_models
        if model not in _warned_models:
            logger.debug(
                f"No tokenizer for '{model}'. Using tiktoken cl100k_base. Use results only as estimates."
            )
            _warned_models |= {model}
        return tiktoken.get_encoding("cl100k_base")


# perf trick: start background thread that pre-loads the gpt-4 and gpt-5 tokenizers
#             needs logic to wait for the tokenizer to be ready if requested before loaded
# threading.Thread(target=get_tokenizer, args=("gpt-4",), daemon=True).start()
# threading.Thread(target=get_tokenizer, args=("gpt-5",), daemon=True).start()


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
    count = len(tokenizer.encode(content, disallowed_special=[]))
    _token_cache[cache_key] = count

    # Limit cache size by removing oldest entries if needed
    if len(_token_cache) > 1000:
        # Remove first item (oldest in insertion order)
        _token_cache.pop(next(iter(_token_cache)))

    return count
