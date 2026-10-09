"""Pre-deadline reminders must reach the normal tool-executing conversation."""

from unittest.mock import patch

import pytest

from gptme.llm.models import get_model
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.autocompact.hook import autocompact_hook


@pytest.fixture
def manager(tmp_path):
    return LogManager(
        [
            Message("system", "system"),
            Message("user", "fix the test"),
            Message("assistant", "working"),
        ],
        logdir=tmp_path / "conversation",
    )


def run_hook(manager, tokens):
    with (
        patch(
            "gptme.tools.autocompact.hook.get_default_model",
            return_value=get_model("gpt-4"),
        ),
        patch("gptme.tools.autocompact.hook.get_context_budget", return_value=1000),
        patch("gptme.tools.autocompact.hook.should_auto_compact", return_value="none"),
        patch(
            "gptme.tools.autocompact.hook.measure_context_tokens",
            return_value=tokens,
            create=True,
        ),
    ):
        return list(autocompact_hook(manager))


def test_reminder_at_ninety_percent_is_provider_visible(manager):
    reminders = run_hook(manager, 900)
    assert len(reminders) == 1
    reminder = reminders[0]
    assert isinstance(reminder, Message)
    assert reminder.role == "system"
    assert not reminder.ui_only
    assert "Objective" in reminder.content
    assert "Decisions" in reminder.content
    assert "Next move" in reminder.content
    assert "durable" in reminder.content
    assert manager.current_view is None


@pytest.mark.parametrize("tokens", [899, 1000, 1100])
def test_no_reminder_outside_warning_band(manager, tokens):
    assert run_hook(manager, tokens) == []


def test_reminder_is_one_shot_across_growth_and_reload(manager):
    reminder = run_hook(manager, 950)[0]
    manager.append(reminder)
    manager.append(Message("assistant", "notes saved"))
    assert run_hook(manager, 980) == []
    reloaded = LogManager.load(manager.logdir)
    assert run_hook(reloaded, 980) == []


def test_new_compaction_view_rearms_reminder(manager):
    reminder = run_hook(manager, 950)[0]
    manager.append(reminder)
    manager.create_view("compact-1", list(manager.log.messages))
    manager.switch_view("compact-1")
    assert len(run_hook(manager, 950)) == 1


def test_pending_tools_do_not_receive_reminder(manager):
    manager.append(Message("assistant", "```shell\necho hi\n```"))
    assert run_hook(manager, 950) == []
    manager.append(Message("system", "hi"))
    assert len(run_hook(manager, 950)) == 1


def test_reminder_includes_project_instructions(manager):
    from unittest.mock import MagicMock

    config = MagicMock()
    config.context.compact_instructions = "Keep the issue ID and branch."
    with patch("gptme.tools.autocompact.hook.get_project_config", return_value=config):
        reminder = run_hook(manager, 950)[0]
    assert "Keep the issue ID and branch." in reminder.content


def test_reminder_uses_anchored_provider_input(manager):
    from gptme.util.context_measurement import anchor_context_usage, input_log_digest

    model = get_model("gpt-4")
    prefix = manager.log.messages[:-1]
    response = Message(
        "assistant",
        "working",
        metadata={"usage": {"input_tokens": 100, "cache_read_tokens": 800}},
    )
    manager.log.messages[-1] = response
    anchor_context_usage(response, len(prefix), input_log_digest(prefix), model.full)
    with (
        patch("gptme.tools.autocompact.hook.get_default_model", return_value=model),
        patch("gptme.tools.autocompact.hook.get_context_budget", return_value=1000),
    ):
        reminders = list(autocompact_hook(manager))
    assert len(reminders) == 1
    assert isinstance(reminders[0], Message)
    assert not reminders[0].ui_only


@pytest.mark.parametrize("entrypoint", ["cli", "server"])
def test_post_tool_path_appends_reminder_without_rewriting_history(manager, entrypoint):
    from gptme.chat import _run_post_tool_compaction

    if entrypoint == "server":
        pytest.importorskip("flask")
        from gptme.server.session_models import ConversationSession
        from gptme.server.session_step import _compact_after_tool_results

    original = list(manager.log.messages)
    reminder = run_hook(manager, 950)[0]
    with patch(
        "gptme.tools.autocompact.hook.autocompact_hook", return_value=iter([reminder])
    ):
        if entrypoint == "cli":
            assert not _run_post_tool_compaction(manager)
        else:
            session = ConversationSession(
                id="test-session", conversation_id="test-reminder"
            )
            with patch("gptme.server.session_step.SessionManager.add_event") as notify:
                _compact_after_tool_results(manager, session, "test-reminder")
            assert notify.call_count == 1
    assert manager.log.messages[:-1] == original
    assert manager.log.messages[-1] == reminder
    assert manager.current_view is None


def test_same_view_after_usage_dip_does_not_repeat(manager):
    manager.append(run_hook(manager, 950)[0])
    assert run_hook(manager, 800) == []
    assert run_hook(manager, 950) == []
