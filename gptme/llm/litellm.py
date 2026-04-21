"""LiteLLM-backed OpenAI-compatible client.

Provides ``LiteLLMClient``, a duck-typed object that exposes ``chat.completions.create``
(and a minimal subset of the OpenAI client surface) but dispatches every call to
``litellm.completion()``. Installed into ``llm_openai.clients`` when the user selects
the ``litellm`` provider, which lets the existing OpenAI-shaped chat/stream code in
``llm_openai.py`` route to any of LiteLLM's 100+ providers without modification.

Authentication: LiteLLM resolves provider-specific API keys from the environment
(``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY``, ``AWS_*``, ``GROQ_API_KEY``, etc.). Users
pass the fully-prefixed model name via ``gptme -m litellm/<provider>/<model>``. The
``litellm/`` prefix is stripped upstream by ``_get_base_model`` before the model name
reaches this shim, so ``litellm.completion`` receives the provider-prefixed name
(e.g. ``anthropic/claude-3-5-sonnet-20241022``) that it knows how to route.

See https://docs.litellm.ai/docs/providers for supported model prefixes.
"""

from __future__ import annotations

from typing import Any


class _LiteLLMChatCompletions:
    """Dispatches ``.create(**kwargs)`` to ``litellm.completion(**kwargs)``."""

    def create(self, **kwargs: Any) -> Any:
        import litellm  # lazy import — ``litellm`` is an optional extra

        return litellm.completion(**kwargs)


class _LiteLLMChat:
    def __init__(self) -> None:
        self.completions = _LiteLLMChatCompletions()


class LiteLLMClient:
    """Duck-typed OpenAI-compatible client backed by ``litellm.completion``.

    Only the subset of the OpenAI v1 client surface actually exercised by gptme's
    ``llm_openai`` code path is implemented. Attributes present purely so that
    helper checks (e.g. ``_is_proxy``) don't raise.
    """

    # Exposed for ``_is_proxy`` in llm_openai.py. LiteLLM is in-process; no base_url.
    base_url: str | None = None
    api_key: str | None = None

    def __init__(self) -> None:
        self.chat = _LiteLLMChat()
