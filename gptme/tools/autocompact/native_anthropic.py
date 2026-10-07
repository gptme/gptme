"""Anthropic provider-native on-demand compaction.

When the model supports the ``compaction`` capability (Models API lookup), the
old prefix of the conversation is summarized by the provider itself via the
``compact-2026-09-04`` beta. The provider returns a single signed
``compaction`` content block, which is persisted losslessly in the checkpoint
message's metadata and replayed verbatim (as the first content block, with the
beta header) on every later Anthropic request.

When capability lookup fails or the provider returns no usable block, callers
fall back to the generic LLM checkpoint path.
"""

import logging
from typing import TYPE_CHECKING, cast

from ...message import Message, MessageMetadata

if TYPE_CHECKING:
    from anthropic.types.beta import (
        BetaCompactionConfigParam,
        BetaMessageParam,
    )

from anthropic._types import omit

if TYPE_CHECKING:
    from anthropic._client import Anthropic

logger = logging.getLogger(__name__)

COMPACT_BETA = "compact-2026-09-04"

# Metadata key carrying the signed compaction block (round-tripped verbatim).
COMPACTION_BLOCK_KEY = "anthropic_compaction_block"

_capability_cache: dict[str, bool] = {}


def compaction_block_of(message: Message) -> dict | None:
    """Return the signed compaction block carried by ``message``, if any."""
    block = (message.metadata or {}).get(COMPACTION_BLOCK_KEY)
    return block if isinstance(block, dict) else None


def conversation_has_compaction_block(messages: list[Message]) -> bool:
    return any(compaction_block_of(m) for m in messages)


def _client() -> "Anthropic | None":
    from ...llm import llm_anthropic

    # Direct client only: the gptme gateway path must not be enabled until the
    # gateway is verified to forward the beta header and compaction parameter.
    return llm_anthropic.get_client()


def anthropic_compaction_supported(model: str) -> bool:
    """Capability-gate native compaction on the Models API.

    Any lookup failure returns False so callers fall back to the generic
    checkpoint; capability data is cached per model id.
    """
    cached = _capability_cache.get(model)
    if cached is not None:
        return cached
    client = _client()
    if client is None:
        return False
    try:
        info = client.beta.models.retrieve(model)
        supported = bool(
            info.capabilities is not None
            and info.capabilities.compaction is not None
            and info.capabilities.compaction.supported
            and info.capabilities.compaction.summarize.supported
        )
    except Exception as e:
        logger.debug("Compaction capability lookup failed for %s: %s", model, e)
        supported = False
    _capability_cache[model] = supported
    return supported


def anthropic_native_compact(
    prefix_msgs: list[Message],
    model: str,
    instructions: str | None = None,
) -> Message | None:
    """Compact ``prefix_msgs`` via the provider and return the block message.

    Returns the synthetic assistant message carrying the signed compaction
    block, or None when the provider is unavailable, the request fails, or the
    response carries no usable signed block. Callers fall back to the generic
    checkpoint path on None.
    """
    from ...llm import llm_anthropic

    client = _client()
    if client is None:
        return None

    # Reuse the provider's message conversion so the summarized prefix is the
    # exact provider-visible shape (system extraction, files, tool formats).
    # Conversion happens inside the try so any conversion failure falls back
    # to the generic checkpoint path instead of raising out of the caller.
    try:
        messages_dicts, system_messages, _ = llm_anthropic._prepare_messages_for_api(
            prefix_msgs, None, model
        )
    except Exception as e:
        logger.warning("Anthropic native compaction conversion failed: %s", e)
        return None

    compaction_param: BetaCompactionConfigParam = {"type": "summarize"}
    if instructions and instructions.strip():
        compaction_param["instructions"] = instructions

    try:
        response = client.beta.messages.create(
            model=model,
            messages=cast("list[BetaMessageParam]", messages_dicts),
            system=system_messages or omit,
            max_tokens=1024,
            betas=[COMPACT_BETA],
            compaction=compaction_param,
        )
    except Exception as e:
        logger.warning("Anthropic native compaction request failed: %s", e)
        return None

    block = next(
        (b for b in response.content if getattr(b, "type", None) == "compaction"),
        None,
    )
    signature = getattr(block, "signature", None) if block is not None else None
    if block is None or not signature:
        logger.warning("Anthropic native compaction returned no signed block")
        return None

    block_dict = {
        "type": "compaction",
        "content": getattr(block, "content", None),
        "encrypted_content": getattr(block, "encrypted_content", None),
        "signature": signature,
    }
    summarized = len(messages_dicts)
    summary_text = getattr(block, "content", None) or ""
    body = (
        f"[Conversation compacted via Anthropic native compaction: "
        f"{summarized} messages summarized into a signed provider block.]\n\n"
        f"{summary_text}"
        if summary_text
        else f"[Conversation compacted via Anthropic native compaction: "
        f"{summarized} messages summarized into a signed provider block.]"
    )
    return Message(
        "assistant",
        body,
        metadata=cast(MessageMetadata, {COMPACTION_BLOCK_KEY: block_dict}),
    )
