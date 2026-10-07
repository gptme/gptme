"""Tests for lossless tool-result recall after checkpoint compaction."""

from gptme.logmanager import LogManager
from gptme.message import Message


def _messages() -> list[Message]:
    return [
        Message("system", "system prompt"),
        Message("assistant", "old tool call"),
        Message("system", "0123456789", call_id="call-old"),
        Message("assistant", "recent tool call"),
        Message("system", "recent result", call_id="call-recent"),
    ]


def test_dropped_result_stubs_use_stable_master_message_ids(tmp_path):
    from gptme.tools.autocompact.resume import _build_dropped_result_stubs

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    stub = _build_dropped_result_stubs(
        manager,
        retained=[messages[0], messages[3], messages[4]],
        model="gpt-4",
    )

    assert stub is not None
    assert "[result #3," in stub.content
    assert "[result #5," not in stub.content
    assert "recall_result(3)" in stub.content


def test_checkpoint_view_keeps_stub_for_dropped_tool_result(tmp_path):
    from gptme.tools.autocompact.resume import _resume_via_llm

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    checkpoint = Message("assistant", "## Objective\nKeep building the recall path.")

    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=True,
            keep_recent_tokens=0,
            checkpoint_response=checkpoint,
        )
    )

    contents = [message.content for message in manager.log.messages]
    assert any("[result #3," in content for content in contents), contents
    assert any("[result #5," in content for content in contents), contents
    assert "0123456789" not in contents
    assert "recent result" not in contents


def test_recompaction_rebuilds_one_result_catalog(tmp_path):
    from gptme.tools.autocompact.resume import _resume_via_llm

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=True,
            keep_recent_tokens=0,
            checkpoint_response=Message("assistant", "## Objective\nFirst checkpoint."),
        )
    )
    manager.append(Message("user", "Continue after the first checkpoint."))
    manager.append(Message("assistant", "Made more progress."))
    first_view = list(manager.log.messages)
    list(
        _resume_via_llm(
            manager,
            first_view,
            use_view_branch=True,
            keep_recent_tokens=500,
            checkpoint_response=Message(
                "assistant", "## Objective\nSecond checkpoint."
            ),
        )
    )

    catalogs = [
        message.content
        for message in manager.log.messages
        if message.content.startswith("Tool results dropped from the active context")
    ]
    assert len(catalogs) == 1
    assert "[result #3," in catalogs[0]
    assert "[result #5," in catalogs[0]
    assert any(
        "Second checkpoint" in message.content for message in manager.log.messages
    )


def test_recall_result_reads_and_pages_lossless_master_result(tmp_path):
    from gptme.tools.recall import recall_result

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    first = recall_result(3, start_char=0, max_chars=4)
    second = recall_result(3, start_char=4, max_chars=20)

    assert "0123" in first
    assert "characters 0-4 of 10" in first
    assert "456789" in second
    assert "characters 4-10 of 10" in second


def test_recall_result_rejects_non_result_and_invalid_ranges(tmp_path):
    from gptme.tools.recall import recall_result

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    assert "not a tool result" in recall_result(2)
    assert "does not exist" in recall_result(99)
    assert "start_char must be" in recall_result(3, start_char=-1)
    assert "max_chars must be" in recall_result(3, max_chars=0)


def test_recall_tool_is_read_only_and_available():
    from gptme.tools import get_available_tools

    recall = next(
        tool for tool in get_available_tools(include_mcp=False) if tool.name == "recall"
    )
    assert recall.read_only is True
    assert recall.functions
    assert [function.name for function in recall.functions] == ["recall_result"]
