"""Saved checkpoint requests retain task continuation across runtime restarts."""

from unittest.mock import patch

import pytest

from gptme.logmanager import LogManager
from gptme.message import Message


@pytest.mark.parametrize("needs_continuation", [False, True])
def test_cli_saved_checkpoint_resumes_original_task(tmp_path, needs_continuation):
    import importlib

    chat = importlib.import_module("gptme.chat")
    request = Message(
        "user",
        "Create checkpoint",
        metadata={
            "compaction_checkpoint_view": "",
            "compaction_checkpoint_needs_continuation": needs_continuation,
        },
    )
    manager = LogManager(
        [Message("system", "System"), Message("user", "Do work"), request],
        logdir=tmp_path / "conversation",
    )
    manager.write()
    manager = LogManager.load(manager.logdir, lock=False)
    inputs = []

    def fake_step(log, *args, **kwargs):
        inputs.append(list(log.messages))
        return [Message("assistant", "Checkpoint" if len(inputs) == 1 else "Done")]

    def apply_checkpoint(manager, source_messages, **kwargs):
        view = manager.get_next_view_name()
        manager.create_view(view, source_messages + [kwargs["checkpoint_response"]])
        manager.switch_view(view)
        yield from ()
        return True

    with (
        patch.object(chat, "step", side_effect=fake_step),
        patch.object(chat, "get_default_model", return_value=None),
        patch.object(chat, "trigger_hook", return_value=[]),
        patch("gptme.tools.autocompact.hook.should_auto_compact", return_value="none"),
        patch("gptme.tools.autocompact.hook.get_default_model", return_value=None),
        patch("gptme.tools.autocompact.hook.get_project_config", return_value=None),
        patch(
            "gptme.tools.autocompact.hook._resume_via_llm", side_effect=apply_checkpoint
        ),
        patch("gptme.tools.autocompact.hook.trigger_hook", return_value=[]),
        patch("gptme.tools.autocompact.hook.append_compaction_event"),
    ):
        chat._process_message_conversation(manager, False, "markdown", "gpt-4")

    assert manager.current_view is not None
    assert len(inputs) == (2 if needs_continuation else 1)
    if needs_continuation:
        assert inputs[1][-1].content == "Checkpoint"
        assert manager.log.messages[-1].content == "Done"
