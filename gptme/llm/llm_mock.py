"""Built-in offline mock provider.

Provides deterministic, no-auth canned responses for tests, demos, and offline
development. Unlike the ``local`` provider (which still talks to an
OpenAI-compatible HTTP endpoint), the ``mock`` provider needs no server, no API
key, and no network — responses are computed in-process.

Scripted models (see ``MODELS["mock"]`` in :mod:`gptme.llm.models.data`):

- ``mock/echo``    — echoes the last user message back, prefixed with ``Echo: ``.
- ``mock/static``  — returns a fixed canned string.

Use these to exercise the LLM plumbing (reply, streaming, model listing) without
hitting a real provider.
"""

from collections.abc import Generator

from ..message import Message, MessageMetadata

# Fixed response for the ``mock/static`` model.
STATIC_RESPONSE = "This is a static mock response."


def _last_user_text(messages: list[Message]) -> str:
    """Return the content of the most recent user message (empty if none)."""
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return ""


def _generate(messages: list[Message], model: str) -> str:
    """Compute the canned response for a mock model.

    ``model`` is the fully-qualified name (e.g. ``"mock/echo"``); the bare model
    name after the provider prefix selects the scripted behaviour.
    """
    base_model = model.split("/", 1)[1] if "/" in model else model
    if base_model == "echo":
        return f"Echo: {_last_user_text(messages)}"
    if base_model == "static":
        return STATIC_RESPONSE
    raise ValueError(
        f"Unknown mock model: {model!r} (available: mock/echo, mock/static)"
    )


def chat(
    messages: list[Message],
    model: str,
    tools=None,
    output_schema=None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> tuple[str, MessageMetadata | None]:
    """Return a deterministic canned response and zero-cost metadata."""
    return _generate(messages, model), {"model": model}


def stream(
    messages: list[Message],
    model: str,
    tools=None,
    output_schema=None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> Generator[str, None, MessageMetadata | None]:
    """Stream the canned response one whitespace-delimited token at a time."""
    response = _generate(messages, model)
    # Yield word-by-word so consumers exercise the incremental-chunk path rather
    # than receiving the whole string at once. Re-insert the separating spaces so
    # that "".join(chunks) reconstructs the response exactly (matches chat()).
    words = response.split(" ")
    for i, word in enumerate(words):
        yield word if i == 0 else " " + word
    return {"model": model}
