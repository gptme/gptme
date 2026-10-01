"""Anchor provider input usage to an unchanged stored prefix."""

import hashlib
import json

from ..message import Message, len_tokens


def _digest_chunks(message: Message):
    fields = message.to_dict(
        keys=(
            "role",
            "content",
            "files",
            "file_hashes",
            "ui_only",
            "ephemeral_ttl",
            "call_id",
        )
    )
    if message.metadata and "prompt_generation" in message.metadata:
        fields["prompt_generation"] = message.metadata["prompt_generation"]
    yield json.dumps(fields, sort_keys=True).encode()
    yield b"\x00"


def prefix_digests(messages: list[Message]) -> list[str]:
    """Digest of every prefix ``messages[:i]``, computed in a single pass."""
    digest = hashlib.sha256()
    digests = [digest.copy().hexdigest()]
    for message in messages:
        for chunk in _digest_chunks(message):
            digest.update(chunk)
        digests.append(digest.copy().hexdigest())
    return digests


def input_log_digest(messages: list[Message]) -> str:
    """Fingerprint input structure, excluding timestamps and response metadata."""
    return prefix_digests(messages)[-1]


def anchor_context_usage(
    response: Message, count: int, digest: str, model: str
) -> None:
    """Persist the stored-input anchor only for responses with provider usage."""
    if response.metadata and response.metadata.get("usage"):
        response.metadata["input_log_messages"] = count
        response.metadata["input_log_digest"] = digest
        response.metadata.setdefault("model", model)


def measure_context_tokens(messages: list[Message], model: str) -> int:
    """Last valid provider input + estimated response/tail; text-only fallback.

    UsageData normalizes input_tokens to *uncached* input for every provider,
    so cache reads and writes must be included. The response itself was not in
    that request: count it along with subsequent messages. A prefix digest keeps
    usage from surviving a compaction view, edit, or switch to a sibling branch.
    Legacy logs without an anchor use the estimate until a new response arrives.
    """
    visible = [message for message in messages if not message.ui_only]
    # Single-pass prefix digests: scanning candidate anchors must not
    # re-serialize the whole log per stale response (quadratic on edits).
    digests = prefix_digests(messages)
    for index in range(len(messages) - 1, -1, -1):
        response = messages[index]
        metadata = response.metadata or {}
        usage = metadata.get("usage")
        count = metadata.get("input_log_messages")
        expected = metadata.get("input_log_digest")
        if response.role != "assistant" or response.ui_only or not usage:
            continue
        if count != index or not expected:
            continue
        if digests[index] != expected:
            continue
        if not _model_matches(metadata.get("model"), model):
            continue
        counts = (
            usage.get("input_tokens", 0),
            usage.get("cache_read_tokens", 0),
            usage.get("cache_creation_tokens", 0),
        )
        if any(not isinstance(value, int) or value < 0 for value in counts):
            continue
        total = sum(counts)
        if total <= 0:
            continue
        tail = [message for message in messages[index:] if not message.ui_only]
        return total + len_tokens(tail, model)
    return len_tokens(visible, model)


def _model_matches(stored: str | None, model: str) -> bool:
    """Anchor model must be the same model; bare provider-reported names match
    the qualified form used at compare time (e.g. Anthropic records
    ``claude-sonnet-4-5`` while we compare against ``anthropic/claude-sonnet-4-5``)."""
    if stored is None:
        return True
    return stored == model or model.endswith("/" + stored)
