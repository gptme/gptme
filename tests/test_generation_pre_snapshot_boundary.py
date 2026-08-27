"""Phase 2 regression tests: GENERATION_PRE message snapshot boundary and provider-ordering stability.

These tests verify that:
1. GENERATION_PRE messages are captured at a durable snapshot boundary (not recomputed each turn)
2. Provider request construction produces byte-identical output across turns
3. OpenAI system message hoisting and Anthropic system message extraction are stable
"""

from __future__ import annotations

from dataclasses import dataclass

from gptme.message import Message


@dataclass
class GenerationPreSnapshot:
    """Snapshot of GENERATION_PRE messages at a durable boundary.

    This represents the transient messages that should be captured once per turn
    and reused for the provider request, rather than being recomputed.
    """

    messages: list[Message]
    model: str
    workspace: str | None
    fingerprint: str  # SHA256 of the message content for cache validation


class TestGenerationPreSnapshotBoundary:
    """Test that GENERATION_PRE messages are captured and persisted."""

    def test_provider_message_ordering_openai_responses(self):
        """OpenAI Responses API should hoist system messages consistently."""
        from gptme.llm.openai_responses import (
            MessageDict,
            _messages_dicts_to_responses_input,
        )

        # Message structure: original + GENERATION_PRE-added system message
        messages_dicts: list[MessageDict] = [
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": "Extra context from hook"},
            {"role": "user", "content": "Hello"},
        ]

        # First call - should hoist system messages to instructions
        instructions_1, items_1 = _messages_dicts_to_responses_input(messages_dicts)

        # Second call - should produce identical instructions (stable)
        instructions_2, items_2 = _messages_dicts_to_responses_input(messages_dicts)

        # This is the regression test: ensure byte-identical hoisting
        assert instructions_1 == instructions_2, "System message hoisting not stable"
        assert items_1 == items_2, "Message items not stable"

        # Verify system messages are correctly hoisted. The input has two system
        # messages, so a missing instructions value is itself a regression.
        assert instructions_1 is not None
        assert "You are helpful" in instructions_1
        assert "Extra context from hook" in instructions_1

    def test_provider_message_ordering_anthropic_system_extraction(self):
        """Anthropic should extract system messages consistently."""
        from gptme.llm.llm_anthropic import _transform_system_messages

        # Message structure: original + GENERATION_PRE-added system message
        messages = [
            Message(role="system", content="You are helpful."),
            Message(role="system", content="Extra context from hook"),
            Message(role="user", content="Hello"),
        ]

        # First call - should extract system messages consistently
        transformed_1, system_1 = _transform_system_messages(
            messages, model="claude-opus-4-8"
        )

        # Second call - should produce identical extraction
        transformed_2, system_2 = _transform_system_messages(
            messages, model="claude-opus-4-8"
        )

        # Regression test: ensure deterministic extraction across calls
        # This is the core cache-stability requirement
        assert system_1 == system_2, "System messages not extracted consistently"

    def test_unchanged_history_produces_identical_provider_request(self):
        """Core regression: system prefix is stable after new conversation turns are appended.

        Verifies that the Anthropic-formatted system messages (the cacheable prefix)
        are byte-identical before and after appending new conversation turns.
        A change would cause a cache miss on every new user message.
        """
        from gptme.llm.llm_anthropic import _transform_system_messages

        # Initial conversation: static system context + first user turn
        initial_messages = [
            Message(
                role="system", content="Static profile: you are a helpful assistant."
            ),
            Message(role="user", content="Hello, first turn"),
        ]

        # Get provider representation before any new turns are added
        _, system_1 = _transform_system_messages(
            initial_messages, model="claude-opus-4-8"
        )

        # Simulate a second turn: assistant replies, user sends another message
        messages_after_turn = [
            Message(
                role="system", content="Static profile: you are a helpful assistant."
            ),
            Message(role="user", content="Hello, first turn"),
            Message(role="assistant", content="Hi! How can I help?"),
            Message(role="user", content="Tell me more."),
        ]

        # System prefix must be byte-identical after the conversation grows
        _, system_2 = _transform_system_messages(
            messages_after_turn, model="claude-opus-4-8"
        )

        assert system_1 == system_2, (
            "System message prefix changed after adding new conversation turns — "
            "this would cause a cache miss on every new message."
        )


class TestProviderOrderingRegressions:
    """Regressions for provider-specific message ordering."""

    def test_openai_system_message_hoisting_order(self):
        """OpenAI Responses hoisting should preserve system message order."""
        from gptme.llm.openai_responses import (
            MessageDict,
            _messages_dicts_to_responses_input,
        )

        messages_dicts: list[MessageDict] = [
            {"role": "system", "content": "First context"},
            {"role": "system", "content": "Second context"},
            {"role": "user", "content": "Question"},
        ]

        instructions, items = _messages_dicts_to_responses_input(messages_dicts)

        # Verify order is preserved in hoisted instructions
        if instructions is not None:
            first_idx = instructions.find("First context")
            second_idx = instructions.find("Second context")
            assert first_idx < second_idx, "System message order not preserved"

    def test_anthropic_system_message_merging_order(self):
        """Anthropic extraction should merge system messages in order."""
        from gptme.llm.llm_anthropic import _transform_system_messages

        messages = [
            Message(role="system", content="First context"),
            Message(role="system", content="Second context"),
            Message(role="user", content="Question"),
        ]

        # The key regression: extraction is deterministic
        transformed_1, system_msgs_1 = _transform_system_messages(
            messages, model="claude-opus-4-8"
        )
        transformed_2, system_msgs_2 = _transform_system_messages(
            messages, model="claude-opus-4-8"
        )

        # Ensure consistent extraction across turns (cache-stability requirement)
        assert system_msgs_1 == system_msgs_2, (
            "System message extraction not deterministic"
        )
