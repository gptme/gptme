"""Tests for util/tokens.py — token counting, caching, and tokenizer selection."""

import hashlib

import pytest


def test_hash_content_deterministic():
    """_hash_content returns consistent SHA-256 hex digest."""
    from gptme.util.tokens import _hash_content

    result = _hash_content("hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert result == expected


def test_hash_content_different_inputs():
    """Different inputs produce different hashes."""
    from gptme.util.tokens import _hash_content

    assert _hash_content("hello") != _hash_content("world")
    assert _hash_content("") != _hash_content(" ")


def test_hash_content_empty_string():
    """Empty string produces valid hash."""
    from gptme.util.tokens import _hash_content

    result = _hash_content("")
    assert len(result) == 64  # SHA-256 hex digest length
    assert result == hashlib.sha256(b"").hexdigest()


def test_hash_content_unicode():
    """Unicode content is handled correctly."""
    from gptme.util.tokens import _hash_content

    result = _hash_content("こんにちは世界")
    expected = hashlib.sha256("こんにちは世界".encode()).hexdigest()
    assert result == expected


def test_get_tokenizer_gpt4o():
    """gpt-4o models use o200k_base encoding."""
    from gptme.util.tokens import get_tokenizer

    enc = get_tokenizer("gpt-4o")
    assert enc.name == "o200k_base"

    # Variants should also match
    enc2 = get_tokenizer("gpt-4o-mini")
    assert enc2.name == "o200k_base"


def test_get_tokenizer_unknown_model():
    """Unknown models fall back to cl100k_base."""
    from gptme.util.tokens import get_tokenizer

    enc = get_tokenizer("totally-unknown-model-xyz")
    assert enc.name == "cl100k_base"


def test_get_tokenizer_known_model():
    """Known OpenAI models get their proper tokenizer."""
    from gptme.util.tokens import get_tokenizer

    enc = get_tokenizer("gpt-4")
    assert enc.name == "cl100k_base"


def test_len_tokens_string():
    """len_tokens works on plain strings."""
    from gptme.util.tokens import len_tokens

    count = len_tokens("hello world", model="gpt-4")
    assert isinstance(count, int)
    assert count > 0
    assert count < 10  # "hello world" is 2-3 tokens


def test_len_tokens_empty_string():
    """Empty string returns 0 tokens."""
    from gptme.util.tokens import len_tokens

    count = len_tokens("", model="gpt-4")
    assert count == 0


def test_len_tokens_message():
    """len_tokens works on Message objects."""
    from gptme.message import Message
    from gptme.util.tokens import len_tokens

    msg = Message(role="user", content="hello world")
    count = len_tokens(msg, model="gpt-4")
    assert isinstance(count, int)
    assert count > 0


def test_len_tokens_message_list():
    """len_tokens sums tokens across a list of messages."""
    from gptme.message import Message
    from gptme.util.tokens import len_tokens

    msgs = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="world"),
    ]
    total = len_tokens(msgs, model="gpt-4")
    individual = sum(len_tokens(m, model="gpt-4") for m in msgs)
    assert total == individual


def test_len_tokens_empty_list():
    """Empty list returns 0 tokens."""
    from gptme.util.tokens import len_tokens

    count = len_tokens([], model="gpt-4")
    assert count == 0


def test_len_tokens_caching():
    """Repeated calls with same content use cache."""
    from gptme.util.tokens import _token_cache, len_tokens

    content = "test caching behavior unique string 12345"
    model = "gpt-4"

    # Clear any existing cache entry
    from gptme.util.tokens import _hash_content

    cache_key = (_hash_content(content), model)
    _token_cache.pop(cache_key, None)

    # First call computes and caches
    count1 = len_tokens(content, model=model)
    assert cache_key in _token_cache

    # Second call returns same result (from cache)
    count2 = len_tokens(content, model=model)
    assert count1 == count2


def test_len_tokens_long_content():
    """Token counting works on longer content."""
    from gptme.util.tokens import len_tokens

    # A paragraph of text
    content = "The quick brown fox jumps over the lazy dog. " * 50
    count = len_tokens(content, model="gpt-4")
    assert count > 100  # Should be many tokens


def test_len_tokens_invalid_type():
    """Non-string, non-Message, non-list raises AssertionError."""
    from gptme.util.tokens import len_tokens

    with pytest.raises(AssertionError):
        len_tokens(42, model="gpt-4")  # type: ignore
