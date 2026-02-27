"""Tests for token_awareness hook."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from gptme.hooks.token_awareness import (
    WARNING_INTERVAL,
    WARNING_PERCENTAGES,
    _last_warning_tokens_var,
    _message_counts_var,
    _token_totals_var,
    add_token_budget,
    add_token_usage_warning,
)
from gptme.message import Message


@pytest.fixture(autouse=True)
def reset_token_state():
    """Reset context-local token tracking state before each test."""
    _token_totals_var.set(None)
    _message_counts_var.set(None)
    _last_warning_tokens_var.set(None)
    yield
    _token_totals_var.set(None)
    _message_counts_var.set(None)
    _last_warning_tokens_var.set(None)


def _make_model(context: int = 200_000):
    """Create a mock model with specified context window."""
    model = MagicMock()
    model.context = context
    model.model = "test-model"
    return model


class TestAddTokenBudget:
    """Tests for add_token_budget SESSION_START hook."""

    def test_yields_budget_message(self, tmp_path: Path):
        """Yields system message with token budget tag."""
        with patch(
            "gptme.llm.models.get_default_model",
            return_value=_make_model(128_000),
        ):
            msgs = list(add_token_budget(tmp_path, None, []))
        assert len(msgs) == 1
        msg = cast(Message, msgs[0])
        assert msg.role == "system"
        assert "128000" in msg.content
        assert "budget:token_budget" in msg.content
        assert msg.hide is True

    def test_no_model_yields_nothing(self, tmp_path: Path):
        """No message when no model is loaded."""
        with patch(
            "gptme.llm.models.get_default_model",
            return_value=None,
        ):
            msgs = list(add_token_budget(tmp_path, None, []))
        assert msgs == []

    def test_with_workspace(self, tmp_path: Path):
        """Works with workspace parameter."""
        with patch(
            "gptme.llm.models.get_default_model",
            return_value=_make_model(200_000),
        ):
            msgs = list(add_token_budget(tmp_path, tmp_path / "ws", []))
        assert len(msgs) == 1
        assert "200000" in cast(Message, msgs[0]).content

    def test_exception_yields_nothing(self, tmp_path: Path):
        """Gracefully handles exceptions."""
        with patch(
            "gptme.llm.models.get_default_model",
            side_effect=RuntimeError("bad"),
        ):
            msgs = list(add_token_budget(tmp_path, None, []))
        assert msgs == []


class TestAddTokenUsageWarning:
    """Tests for add_token_usage_warning TOOL_EXECUTE_POST hook."""

    def _make_log(self, messages: list[Message] | None = None) -> Any:
        """Create a minimal Log mock (duck-typed for testing)."""
        if messages is None:
            messages = [Message("user", "hello"), Message("assistant", "hi")]
        log = SimpleNamespace(messages=messages)
        return log

    def test_none_log_yields_nothing(self):
        """No warning when log is None."""
        with patch(
            "gptme.llm.models.get_default_model",
            return_value=_make_model(),
        ):
            msgs = list(add_token_usage_warning(None, None, None))  # type: ignore[arg-type]
        assert msgs == []

    def test_no_model_yields_nothing(self):
        """No warning when no model is loaded."""
        with patch(
            "gptme.llm.models.get_default_model",
            return_value=None,
        ):
            log = self._make_log()
            msgs = list(add_token_usage_warning(log, None, None))
        assert msgs == []

    def test_no_workspace_always_warns(self):
        """Without workspace (no log_id), warns on every call."""
        model = _make_model(200_000)
        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=50_000,
            ),
        ):
            log = self._make_log()
            msgs = list(add_token_usage_warning(log, None, None))
        assert len(msgs) == 1
        msg = cast(Message, msgs[0])
        assert msg.role == "system"
        assert "50000/200000" in msg.content
        assert "150000 remaining" in msg.content
        assert msg.hide is True

    def test_with_workspace_first_call_initializes(self, tmp_path: Path):
        """First call with workspace initializes tracking and may warn."""
        model = _make_model(200_000)
        workspace = tmp_path / "ws"
        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=110_000,
            ),
        ):
            log = self._make_log()
            msgs = list(add_token_usage_warning(log, workspace, None))

        # 110k / 200k = 55% → crosses 50% threshold → should warn
        assert len(msgs) == 1
        assert "110000/200000" in cast(Message, msgs[0]).content

    def test_incremental_counting(self, tmp_path: Path):
        """Second call only counts new messages (incremental)."""
        model = _make_model(200_000)
        workspace = tmp_path / "ws"

        msgs1 = [Message("user", "hello"), Message("assistant", "hi")]
        log1: Any = SimpleNamespace(messages=msgs1)

        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=5_000,
            ),
        ):
            list(add_token_usage_warning(log1, workspace, None))

        # Add more messages
        msgs2 = msgs1 + [Message("user", "more"), Message("assistant", "ok")]
        log2: Any = SimpleNamespace(messages=msgs2)

        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=5_000,
            ),
        ):
            # Second call: total = 5000 + 5000 = 10000
            # 10k / 200k = 5% → below thresholds
            # But (10000 - 0) >= 10000 → WARNING_INTERVAL threshold crossed
            msgs = list(add_token_usage_warning(log2, workspace, None))

        assert len(msgs) == 1
        assert "10000/200000" in cast(Message, msgs[0]).content

    def test_no_warning_below_thresholds(self, tmp_path: Path):
        """No warning when usage is below all thresholds."""
        model = _make_model(200_000)
        workspace = tmp_path / "ws"

        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=5_000,
            ),
        ):
            log = self._make_log()
            list(add_token_usage_warning(log, workspace, None))

        # Now call again with no new messages → no token change → no warning
        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=0,
            ),
        ):
            msgs = list(add_token_usage_warning(log, workspace, None))
        assert msgs == []

    def test_percentage_threshold_crossing(self, tmp_path: Path):
        """Warning when crossing a percentage threshold (75%)."""
        model = _make_model(100_000)
        workspace = tmp_path / "ws"

        # First call: 40k / 100k = 40% → below 50% but crosses WARNING_INTERVAL
        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=40_000,
            ),
        ):
            log = self._make_log()
            list(add_token_usage_warning(log, workspace, None))

        # Now we're at 40k, last_warning=40k. Add 36k more → 76k → crosses 75%
        msgs2 = [Message("user", "hi")] * 4
        log2: Any = SimpleNamespace(messages=log.messages + msgs2)

        with (
            patch(
                "gptme.llm.models.get_default_model",
                return_value=model,
            ),
            patch(
                "gptme.hooks.token_awareness.len_tokens",
                return_value=36_000,
            ),
        ):
            msgs = list(add_token_usage_warning(log2, workspace, None))

        assert len(msgs) == 1
        assert "76000/100000" in cast(Message, msgs[0]).content

    def test_exception_yields_nothing(self, tmp_path: Path):
        """Gracefully handles exceptions."""
        with patch(
            "gptme.llm.models.get_default_model",
            side_effect=RuntimeError("bad"),
        ):
            log = self._make_log()
            msgs = list(add_token_usage_warning(log, tmp_path, None))
        assert msgs == []

    def test_warning_constants(self):
        """Verify warning configuration constants."""
        assert WARNING_INTERVAL == 10_000
        assert WARNING_PERCENTAGES == [0.5, 0.75, 0.9, 0.95]
        # All percentages are valid fractions
        for p in WARNING_PERCENTAGES:
            assert 0 < p < 1
