"""Tests for the policy-block budget: episode counter, budget text, hard stop."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools._policy_block import policy_block_message
from gptme.tools.complete import (
    SessionCompleteException,
    auto_reply_hook,
    block_budget_hook,
    count_policy_blocks,
    current_episode,
)

PROMPT = Message("user", "Do the task")
THINK = Message("assistant", "I will think about it.")  # no tool call


def _block(n: int = 0) -> Message:
    return policy_block_message("denylist", f"Command denied: `rm -rf /x{n}`")


def _blocks(n: int) -> list[Message]:
    return [_block(i) for i in range(n)]


def _manager(messages: list[Message]) -> MagicMock:
    manager = MagicMock()
    manager.log.messages = messages
    return manager


def _replies(*args, **kwargs) -> list[Message]:
    out = list(auto_reply_hook(*args, **kwargs))
    assert all(isinstance(m, Message) for m in out)
    return [m for m in out if isinstance(m, Message)]


def _chat(interactive: bool):
    return patch(
        "gptme.config.get_config",
        return_value=SimpleNamespace(chat=SimpleNamespace(interactive=interactive)),
    )


class TestEpisodeCounter:
    def test_counts_blocks_after_prompt(self):
        assert count_policy_blocks([PROMPT, *_blocks(3)]) == 3

    def test_real_user_message_resets(self):
        msgs = [PROMPT, *_blocks(3), Message("user", "try something else"), _block()]
        assert count_policy_blocks(msgs) == 1

    def test_auto_reply_does_not_reset(self):
        auto = Message(
            "user", "<system>No tool call detected in last message.</system>"
        )
        assert count_policy_blocks([PROMPT, *_blocks(2), auto, _block()]) == 3

    def test_excluded_block_kinds_not_counted(self):
        msgs = [
            PROMPT,
            Message("system", "Declined by user"),
            Message("system", "Shellcheck found issues: SC2086"),
            Message("system", "Command denied: `x` (no marker)"),
        ]
        assert count_policy_blocks(msgs) == 0

    def test_episode_without_user_message_is_whole_log(self):
        msgs = [Message("system", "sys prompt"), _block()]
        assert current_episode(msgs) == msgs


class TestBudgetAutoReply:
    @patch("gptme.tools.complete.has_incomplete_todos", return_value=True)
    @patch("gptme.tools.complete.get_incomplete_todos_summary", return_value="- x")
    def test_budget_text_replaces_todo_pressure(self, _s, _h, monkeypatch):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        msgs = [PROMPT, *_blocks(3), THINK]
        out = _replies(_manager(msgs), interactive=False, prompt_queue=[])
        assert len(out) == 1
        assert "3 actions were blocked by policy" in out[0].content
        assert "incomplete todos" not in out[0].content
        assert "No tool call detected in last message" in out[0].content

    @patch("gptme.tools.complete.has_incomplete_todos", return_value=False)
    def test_below_budget_keeps_normal_nudge(self, _h, monkeypatch):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        msgs = [PROMPT, *_blocks(2), THINK]
        out = _replies(_manager(msgs), interactive=False, prompt_queue=[])
        assert "Did you mean to finish?" in out[0].content

    @patch("gptme.tools.complete.has_incomplete_todos", return_value=False)
    def test_budget_zero_disables(self, _h, monkeypatch):
        monkeypatch.setenv("GPTME_BLOCK_BUDGET", "0")
        msgs = [PROMPT, *_blocks(10), THINK]
        out = _replies(_manager(msgs), interactive=False, prompt_queue=[])
        assert "Did you mean to finish?" in out[0].content

    def test_interactive_no_confirm_nudge_is_budget_aware(self, monkeypatch):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        msgs = [PROMPT, *_blocks(3), THINK]
        out = _replies(
            _manager(msgs), interactive=True, prompt_queue=[], no_confirm=True
        )
        assert len(out) == 1
        assert "blocked by policy" in out[0].content
        assert "Please continue" not in out[0].content

    def test_budget_reply_counts_toward_two_reply_exit(self, monkeypatch):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        first = _replies(
            _manager([PROMPT, *_blocks(3), THINK]), interactive=False, prompt_queue=[]
        )[0]
        msgs = [PROMPT, *_blocks(3), THINK, first, THINK, first, THINK]
        with pytest.raises(SessionCompleteException):
            _replies(_manager(msgs), interactive=False, prompt_queue=[])


class TestHardStop:
    def test_stops_at_twice_budget(self, monkeypatch, caplog):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        with _chat(interactive=False), pytest.raises(SessionCompleteException) as e:
            list(block_budget_hook(_manager([PROMPT, *_blocks(6)])))
        assert "policy block budget exhausted: 6 blocks" in str(e.value)
        assert "policy-block-budget-exhausted" in caplog.text

    def test_below_twice_budget_continues(self, monkeypatch):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        with _chat(interactive=False):
            assert list(block_budget_hook(_manager([PROMPT, *_blocks(5)]))) == []

    def test_interactive_never_stops(self, monkeypatch):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        with _chat(interactive=True):
            assert list(block_budget_hook(_manager([PROMPT, *_blocks(20)]))) == []

    def test_budget_zero_disables(self, monkeypatch):
        monkeypatch.setenv("GPTME_BLOCK_BUDGET", "0")
        with _chat(interactive=False):
            assert list(block_budget_hook(_manager([PROMPT, *_blocks(20)]))) == []

    def test_custom_budget(self, monkeypatch):
        monkeypatch.setenv("GPTME_BLOCK_BUDGET", "1")
        with _chat(interactive=False), pytest.raises(SessionCompleteException):
            list(block_budget_hook(_manager([PROMPT, *_blocks(2)])))

    def test_user_message_resets_hard_stop(self, monkeypatch):
        monkeypatch.delenv("GPTME_BLOCK_BUDGET", raising=False)
        msgs = [PROMPT, *_blocks(6), Message("user", "ok, new plan"), _block()]
        with _chat(interactive=False):
            assert list(block_budget_hook(_manager(msgs))) == []
