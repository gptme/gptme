"""Utilities for detecting duplicate content in conversation context.

Plugins that inject context (retrieval, memories, etc.) can use these
helpers to avoid re-injecting content that is already present in the
conversation from other sources (static ``files`` in ``gptme.toml``,
``AGENTS.md``, other plugins, etc.).

Simple one-off check::

    from gptme.util.context_dedup import is_content_in_context

    if not is_content_in_context(doc_content, messages):
        yield Message("system", doc_content)

High-frequency STEP_PRE use (O(1) per check after construction)::

    from gptme.util.context_dedup import ContextDeduplicator

    dedup = ContextDeduplicator(existing_messages)
    for doc in retrieve_context(query):
        if not dedup.is_present(doc["content"]):
            dedup.mark_present(doc["content"])
            yield Message("system", format_doc(doc))
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..message import Message

# Minimum chunk length (chars) considered worth indexing as a standalone fragment.
# Short strings like section headers are too common to use as dedup keys.
_MIN_CHUNK_LEN = 100


def _content_hash(content: str) -> str:
    """128-bit truncated SHA-256 hash of whitespace-normalised content (hex string)."""
    normalised = " ".join(content.split())
    return hashlib.sha256(normalised.encode()).hexdigest()[:32]


def is_content_in_context(content: str, messages: list[Message]) -> bool:
    """Return True if *content* already appears in any of *messages*.

    Performs a whitespace-normalised substring scan: ``content`` is
    considered present if it (after normalisation) is a substring of any
    existing message's normalised content.

    This is the simple, accurate version — O(n * m) where n is the number
    of messages and m is the size of each message.  Suitable for one-off
    checks or short conversations.  For repeated checks inside a STEP_PRE
    hook, use :class:`ContextDeduplicator` instead.

    Args:
        content: The text to look for.
        messages: Existing conversation messages.

    Returns:
        True if the content (or an equivalent normalised form) appears in
        any message.
    """
    if not content.strip():
        return False
    normalised = " ".join(content.split())
    for msg in messages:
        msg_normalised = " ".join(msg.content.split())
        if normalised in msg_normalised:
            return True
    return False


class ContextDeduplicator:
    """Hash-indexed content tracker for efficient duplicate detection.

    Builds a set of SHA-256 hashes from an initial list of messages, then
    allows O(1) ``is_present``/``mark_present`` operations.

    Indexes both the full message content *and* individual paragraph chunks
    (≥ ``_MIN_CHUNK_LEN`` chars) so that documents included as part of a
    larger system message are still detected as already present.

    Typical STEP_PRE usage::

        class MyPlugin:
            def __init__(self) -> None:
                self._dedup: ContextDeduplicator | None = None

            def step_pre(self, manager: LogManager):
                if self._dedup is None:
                    self._dedup = ContextDeduplicator(manager.log)
                else:
                    # Incrementally index any messages added since last step
                    self._dedup.update_from_log(manager.log)

                for doc in retrieve_context(...):
                    if not self._dedup.is_present(doc["content"]):
                        self._dedup.mark_present(doc["content"])
                        yield Message("system", doc["content"])
    """

    def __init__(self, messages: Iterable[Message]) -> None:
        self._hashes: set[str] = set()
        for msg in messages:
            self._index_message(msg)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _chunks(self, content: str) -> list[str]:
        """Split *content* into indexable chunks (paragraphs + full text)."""
        chunks = [content]
        for para in content.split("\n\n"):
            stripped = para.strip()
            if len(stripped) >= _MIN_CHUNK_LEN:
                chunks.append(stripped)
        return chunks

    def _index_message(self, message: Message) -> None:
        """Add all chunks of *message* to the hash set."""
        for chunk in self._chunks(message.content):
            self._hashes.add(_content_hash(chunk))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_present(self, content: str) -> bool:
        """Return True if *content* matches any already-indexed chunk.

        Checks the hash of the full *content* string and — if that misses —
        also checks each paragraph of *content* individually, so partial
        matches (e.g. a short document that is one paragraph of a larger
        indexed message) are detected.

        .. note::
            The secondary paragraph check can produce false positives in the
            reverse direction: if the *incoming* content shares any paragraph
            (≥ 100 chars) with an already-indexed message, the entire incoming
            document is reported as present — even if only that one paragraph
            overlaps.  This is an intentional trade-off: it prevents injecting
            documents whose key content is already in context, at the cost of
            occasionally suppressing a document with mostly-new content.  If
            fine-grained overlap detection is required, use
            :func:`is_content_in_context` instead.
        """
        if not content.strip():
            return False
        if _content_hash(content) in self._hashes:
            return True
        # Secondary check: each paragraph of the incoming content
        for para in content.split("\n\n"):
            stripped = para.strip()
            if len(stripped) >= _MIN_CHUNK_LEN:
                if _content_hash(stripped) in self._hashes:
                    return True
        return False

    def mark_present(self, content: str) -> None:
        """Mark *content* as present so future ``is_present`` calls return True."""
        for chunk in self._chunks(content):
            self._hashes.add(_content_hash(chunk))

    def update(self, message: Message) -> None:
        """Index a single newly-arrived message (e.g. after injection)."""
        self._index_message(message)

    def update_from_log(self, log: Iterable[Message]) -> None:
        """Incrementally index messages not yet seen.

        This is a convenience method for STEP_PRE hooks that keep a
        ``ContextDeduplicator`` across multiple steps: call this at the
        start of each step so the deduplicator stays in sync with any
        messages injected by other hooks.

        Implementation note: re-hashing already-seen content is a no-op
        (set insertion is idempotent) so it is safe but slightly wasteful
        to call this with the full log every step.  For large conversations
        consider tracking which messages have been indexed separately.
        """
        for msg in log:
            self._index_message(msg)
