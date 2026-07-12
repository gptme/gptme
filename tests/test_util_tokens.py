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
    assert enc is not None
    assert enc.name == "o200k_base"

    # Variants should also match
    enc2 = get_tokenizer("gpt-4o-mini")
    assert enc2 is not None
    assert enc2.name == "o200k_base"


def test_get_tokenizer_provider_prefixed_o1():
    """Provider-prefixed models that need prefix stripping get correct tokenizer.

    Regression: openai/o1 should resolve to o200k_base (not cl100k_base),
    which requires stripping the "openai/" prefix before passing to
    tiktoken.encoding_for_model, since tiktoken doesn't know the "openai/"
    prefix.
    """
    from gptme.util.tokens import get_tokenizer

    enc = get_tokenizer("openai/o1")
    assert enc is not None
    assert enc.name == "o200k_base"

    # gpt-4o-mini with prefix should also resolve correctly (handled by
    # the "gpt-4o" fast-path, but verify anyway)
    enc = get_tokenizer("openai/gpt-4o-mini")
    assert enc is not None
    assert enc.name == "o200k_base"

    # gpt-4 with prefix should resolve to cl100k_base correctly
    enc = get_tokenizer("openai/gpt-4")
    assert enc is not None
    assert enc.name == "cl100k_base"

    # Unknown model with prefix should fall back to cl100k_base
    enc = get_tokenizer("openai/totally-unknown-model-xyz")
    assert enc is not None
    assert enc.name == "cl100k_base"


def test_get_tokenizer_unknown_model():
    """Unknown models fall back to cl100k_base."""
    from gptme.util.tokens import get_tokenizer

    enc = get_tokenizer("totally-unknown-model-xyz")
    assert enc is not None
    assert enc.name == "cl100k_base"


def test_get_tokenizer_known_model():
    """Known OpenAI models get their proper tokenizer."""
    from gptme.util.tokens import get_tokenizer

    enc = get_tokenizer("gpt-4")
    assert enc is not None
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


def test_get_tokenizer_returns_none_on_network_failure(monkeypatch):
    """get_tokenizer returns None when tiktoken raises a network-like error."""
    import gptme.util.tokens as tokens_mod

    tokens_mod.get_tokenizer.cache_clear()
    try:
        # Patch _load_encoding (the function that actually downloads) to raise
        monkeypatch.setattr(
            tokens_mod,
            "_load_encoding",
            lambda name: (_ for _ in ()).throw(Exception("Connection timed out")),
        )
        result = tokens_mod.get_tokenizer("some-offline-model")
        assert result is None
    finally:
        tokens_mod.get_tokenizer.cache_clear()


def test_get_tokenizer_timeout_fallback(monkeypatch):
    """get_tokenizer returns None and falls back to char-based when tiktoken hangs.

    Regression test for the local-model / airgapped scenario described in
    gptme Discussion #559: vLLM / Ollama users with a custom base_url would
    block indefinitely on the cl100k_base BPE data download.
    """
    import threading

    import gptme.util.tokens as tokens_mod

    tokens_mod.get_tokenizer.cache_clear()
    try:
        # Simulate a hanging network fetch — block until the test gives up
        hang_event = threading.Event()

        def _hang(name: str):
            hang_event.wait()  # blocks indefinitely during this test
            raise RuntimeError("should not reach here")

        monkeypatch.setattr(tokens_mod, "_load_encoding", _hang)
        # Use a very short timeout so the test doesn't actually wait 5 s
        monkeypatch.setattr(tokens_mod, "_TIKTOKEN_TIMEOUT", 0.05)

        result = tokens_mod.get_tokenizer("ollama/llama3.2")
        assert result is None

        # Unblock the background thread so the executor can shut down cleanly
        hang_event.set()
    finally:
        tokens_mod.get_tokenizer.cache_clear()


def test_get_tokenizer_disabled_by_timeout_zero(monkeypatch):
    """GPTME_TIKTOKEN_TIMEOUT=0 skips tiktoken entirely — no network attempt."""
    import gptme.util.tokens as tokens_mod

    tokens_mod.get_tokenizer.cache_clear()
    try:
        called = []

        def _should_not_be_called(name: str):
            called.append(name)
            raise RuntimeError("tiktoken should not be called when timeout=0")

        monkeypatch.setattr(tokens_mod, "_load_encoding", _should_not_be_called)
        monkeypatch.setattr(tokens_mod, "_TIKTOKEN_TIMEOUT", 0.0)

        result = tokens_mod.get_tokenizer("any-local-model")
        assert result is None
        assert not called, f"_load_encoding was called unexpectedly: {called}"
    finally:
        tokens_mod.get_tokenizer.cache_clear()


def test_len_tokens_fallback_approximation():
    """len_tokens uses ~4 chars/token when tokenizer is unavailable."""
    import gptme.util.tokens as tokens_mod

    # Save originals
    orig_get_tokenizer = tokens_mod.get_tokenizer

    # Clear caches
    orig_get_tokenizer.cache_clear()
    tokens_mod._token_cache.clear()

    try:
        # Replace get_tokenizer with a version that returns None
        tokens_mod.get_tokenizer = lambda model: None  # type: ignore

        content = "a" * 400  # 400 chars → ~100 tokens
        count = tokens_mod.len_tokens(content, model="fallback-test-model")
        assert count == 100  # 400 // 4

        # Empty string
        tokens_mod._token_cache.clear()
        count_empty = tokens_mod.len_tokens("", model="fallback-test-model")
        assert count_empty == 0
    finally:
        # Restore original
        tokens_mod.get_tokenizer = orig_get_tokenizer
        tokens_mod._token_cache.clear()
        orig_get_tokenizer.cache_clear()


def test_len_tokens_approximation_not_cached():
    """Approximated token counts (tokenizer=None) are NOT stored in _token_cache.

    This ensures accurate counts are used after network recovery — if approximations
    were cached, they would persist even after the tokenizer became available again.
    """
    import gptme.util.tokens as tokens_mod

    orig_get_tokenizer = tokens_mod.get_tokenizer
    orig_get_tokenizer.cache_clear()
    tokens_mod._token_cache.clear()

    from gptme.util.tokens import _hash_content

    content = "unique content for no-cache test 99887766"
    model = "offline-model-xyz"
    cache_key = (_hash_content(content), model)

    try:
        tokens_mod.get_tokenizer = lambda m: None  # type: ignore
        tokens_mod.len_tokens(content, model=model)
        # Approximated result must NOT be stored in _token_cache
        assert cache_key not in tokens_mod._token_cache
    finally:
        tokens_mod.get_tokenizer = orig_get_tokenizer
        tokens_mod._token_cache.clear()
        orig_get_tokenizer.cache_clear()


def test_tiktoken_timeout_invalid_env():
    """Invalid GPTME_TIKTOKEN_TIMEOUT value falls back to 5.0 instead of crashing."""
    # Monkeypatching os.environ is not needed here — we test the parser directly
    import os
    import unittest.mock as mock

    from gptme.util.tokens import _parse_tiktoken_timeout

    with mock.patch.dict(os.environ, {"GPTME_TIKTOKEN_TIMEOUT": "not-a-number"}):
        val = _parse_tiktoken_timeout()
    assert val == 5.0


def test_tiktoken_timeout_valid_env():
    """Valid GPTME_TIKTOKEN_TIMEOUT values are parsed correctly."""
    import os
    import unittest.mock as mock

    from gptme.util.tokens import _parse_tiktoken_timeout

    with mock.patch.dict(os.environ, {"GPTME_TIKTOKEN_TIMEOUT": "0"}):
        assert _parse_tiktoken_timeout() == 0.0

    with mock.patch.dict(os.environ, {"GPTME_TIKTOKEN_TIMEOUT": "10.5"}):
        assert _parse_tiktoken_timeout() == 10.5


def test_get_tokenizer_timeout_not_cached(monkeypatch):
    """Timeout results (None) are NOT cached, enabling retries after network recovery."""
    import threading

    import gptme.util.tokens as tokens_mod

    tokens_mod.get_tokenizer.cache_clear()
    model = "local/model-retry-test"
    try:
        # First call: simulate a hang → timeout → None (not cached)
        hang_event = threading.Event()

        def _hang(name: str) -> None:
            hang_event.wait()

        monkeypatch.setattr(tokens_mod, "_load_encoding", _hang)
        monkeypatch.setattr(tokens_mod, "_TIKTOKEN_TIMEOUT", 0.05)

        result1 = tokens_mod.get_tokenizer(model)
        assert result1 is None
        # None must NOT be cached — _tokenizer_cache should still be empty for this model
        assert model not in tokens_mod._tokenizer_cache
        hang_event.set()  # unblock daemon thread

        # Second call: replace with a fast-returning loader to confirm retry happens
        fake_enc = object()
        load_called: list[str] = []

        def _fast(name: str):
            load_called.append(name)
            return fake_enc

        monkeypatch.setattr(tokens_mod, "_load_encoding", _fast)
        monkeypatch.setattr(tokens_mod, "_TIKTOKEN_TIMEOUT", 5.0)

        result2 = tokens_mod.get_tokenizer(model)
        assert result2 is fake_enc, "Expected retry to succeed after timeout"
        assert load_called, (
            "Expected _load_encoding to be called on retry (not served None from cache)"
        )
    finally:
        tokens_mod.get_tokenizer.cache_clear()


def test_get_tokenizer_successful_load_is_cached(monkeypatch):
    """Successful tokenizer loads ARE cached so repeated calls don't reload."""
    import gptme.util.tokens as tokens_mod

    tokens_mod.get_tokenizer.cache_clear()
    model = "gpt-4-cache-test"
    try:
        load_count: list[str] = []
        fake_enc = object()

        def _counting_load(name: str):
            load_count.append(name)
            return fake_enc

        monkeypatch.setattr(tokens_mod, "_load_encoding", _counting_load)

        result1 = tokens_mod.get_tokenizer(model)
        result2 = tokens_mod.get_tokenizer(model)

        assert result1 is fake_enc
        assert result2 is fake_enc
        assert len(load_count) == 1, (
            "Second call should be served from cache, not reload"
        )
    finally:
        tokens_mod.get_tokenizer.cache_clear()
