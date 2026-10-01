"""Anchor provider input usage to an unchanged stored prefix."""

import hashlib
import json

from ..message import Message, len_tokens


def input_log_digest(messages: list[Message]) -> str:
    """Fingerprint input structure, excluding timestamps and response metadata."""
    digest = hashlib.sha256()
    keys = (
        "role",
        "content",
        "files",
        "file_hashes",
        "ui_only",
        "ephemeral_ttl",
        "call_id",
    )
    for message in messages:
        fields = message.to_dict(keys=keys)
        if message.metadata and "prompt_generation" in message.metadata:
            fields["prompt_generation"] = message.metadata["prompt_generation"]
        digest.update(json.dumps(fields, sort_keys=True).encode())
        digest.update(b"\x00")
    return digest.hexdigest()


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
        if input_log_digest(messages[:index]) != expected:
            continue
        if metadata.get("model") not in (None, model):
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
