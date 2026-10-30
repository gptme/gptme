"""Tests for lossless tool-result recall after checkpoint compaction."""

import os
from pathlib import Path

import pytest

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


def test_recall_tool_has_native_execute_handler(tmp_path):
    from gptme.tools import get_available_tools

    messages = _messages()
    LogManager(messages, logdir=tmp_path / "conversation", lock=False).write()

    recall = next(
        tool for tool in get_available_tools(include_mcp=False) if tool.name == "recall"
    )
    assert recall.execute is not None
    result = recall.execute(
        None, None, {"result_id": "3", "start_char": "0", "max_chars": "4"}
    )
    content = (
        result.content
        if isinstance(result, Message)
        else "".join(message.content for message in result)
    )
    assert "0123" in content


def test_multi_message_tool_outputs_are_results(tmp_path):
    from gptme.util.master_context import is_tool_result_message

    messages = [
        Message("system", "system prompt"),
        Message("assistant", "```shell\necho hi\n```"),
        Message("system", "warning: shellcheck note"),
        Message("system", "actual output", call_id="call-1"),
        Message("system", "hook notice", hide=True),
    ]
    assert is_tool_result_message(messages, 2)
    assert is_tool_result_message(messages, 3)
    # Status messages are never tool results
    assert not is_tool_result_message(messages, 4)


def test_manual_compaction_preserves_lossless_master(tmp_path):
    from gptme.tools.autocompact.resume import _resume_via_llm

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=False,
            keep_recent_tokens=0,
            checkpoint_response=Message("assistant", "## Objective\nManual compact."),
        )
    )

    master_contents = [m.content for m in manager.master_log.messages]
    assert "0123456789" in master_contents
    recall_msg = manager.master_log.messages[2]
    assert recall_msg.content == "0123456789"


def test_user_message_quoting_catalog_is_not_filtered(tmp_path):
    from gptme.tools.autocompact.resume import _is_result_stubs_message

    quoted = Message(
        "user",
        "Tool results dropped from the active context remain in the lossless master log.\nNow do X.",
    )
    real = Message(
        "system",
        "Tool results dropped from the active context remain in the lossless master log. Recall one by its stable ID:",
        metadata={"result_stubs": True},
    )
    assert not _is_result_stubs_message(quoted)
    assert _is_result_stubs_message(real)


def test_recall_result_allows_hidden_tool_results(tmp_path):
    from gptme.tools.recall import recall_result

    messages = _messages() + [
        Message("system", "hunter2", call_id="call-secret", hide=True)
    ]
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    assert "hunter2" in recall_result(6)


def test_execute_recall_preserves_source_hide_flag(tmp_path):
    """A recalled hidden result stays assistant-visible but display-hidden."""
    from gptme.tools.recall import execute_recall

    messages = _messages() + [
        Message("system", "hunter2", call_id="call-secret", hide=True)
    ]
    LogManager(messages, logdir=tmp_path / "conversation", lock=False).write()

    recalled = execute_recall(None, None, {"result_id": "6"})
    assert "hunter2" in recalled.content
    assert recalled.hide is True

    # Visible results keep the default display behavior.
    visible = execute_recall(None, None, {"result_id": "3"})
    assert visible.hide is False


def test_dropped_result_stubs_include_hidden_tool_results(tmp_path):
    from gptme.tools.autocompact.resume import _build_dropped_result_stubs

    messages = _messages() + [
        Message("system", "hunter2", call_id="call-secret", hide=True)
    ]
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    stub = _build_dropped_result_stubs(
        manager,
        retained=[messages[0]],
        model="gpt-4",
    )

    assert stub is not None
    assert "recall_result(3)" in stub.content
    assert "recall_result(5)" in stub.content
    assert "recall_result(6)" in stub.content
    assert "hunter2" not in stub.content


def test_resume_keeps_intro_and_checkpoint_adjacent(tmp_path):
    from gptme.tools.autocompact.resume import (
        _find_previous_checkpoint_index,
        _resume_via_llm,
    )

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    checkpoint = Message("assistant", "## Objective\nKeep building the recall path.")

    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=False,
            keep_recent_tokens=0,
            checkpoint_response=checkpoint,
        )
    )

    assert _find_previous_checkpoint_index(manager.log.messages) is not None


def test_append_non_main_branch_does_not_mirror_to_lossless(tmp_path):
    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    manager.preserve_lossless_log()

    manager.branch("experiment")
    manager.append(Message("system", "experiment result", call_id="call-exp"))

    lossless_contents = [m.content for m in manager.master_log.messages]
    assert "experiment result" not in lossless_contents


def test_unknown_codeblock_language_is_not_a_tool_result():
    from gptme.util.master_context import is_tool_result_message

    messages = [
        Message("assistant", "Example:\n```rust\nfn main() {}\n```"),
        Message("system", "ordinary system notice"),
    ]
    assert not is_tool_result_message(messages, 1)


def test_disabled_shell_results_remain_recallable(monkeypatch):
    import gptme.tools
    from gptme.util.master_context import is_tool_result_message

    monkeypatch.setattr(gptme.tools, "_get_loaded_tools", lambda: [])
    messages = [
        Message("assistant", "```shell\necho hi\n```"),
        Message("system", "shellcheck warning"),
        Message("system", "hi"),
    ]
    assert is_tool_result_message(messages, 1)
    assert is_tool_result_message(messages, 2)


def test_lossless_view_appends_survive_reload(tmp_path):
    from gptme.tools.autocompact.resume import _resume_via_llm
    from gptme.tools.recall import recall_result

    messages = _messages()
    logdir = tmp_path / "conversation"
    manager = LogManager(messages, logdir=logdir, lock=False)
    manager.write()
    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=False,
            keep_recent_tokens=0,
            checkpoint_response=Message("assistant", "## Objective\nManual compact."),
        )
    )
    manager.create_view("compacted-001", list(manager.log.messages))
    manager.switch_view("compacted-001")
    manager.append(Message("assistant", "later tool call"))
    manager.append(Message("system", "later result", call_id="call-later"))
    result_id = len(manager.master_log.messages)

    reloaded = LogManager.load(logdir, lock=False)
    assert reloaded.master_log.messages[result_id - 1].content == "later result"
    assert "later result" in recall_result(result_id)
    assert "0123456789" in recall_result(3)


def test_resume_catalog_rebuild_does_not_exceed_budget(tmp_path, monkeypatch):
    """A catalog dropped by the budget guard must not be re-appended over budget.

    The catalog is rebuilt after tail selection so remaining results are not
    stubbed twice. That rebuild used to ignore a prior drop, so a checkpoint
    that fit without the catalog went back over budget once the catalog
    returned.
    """
    from types import SimpleNamespace

    from gptme.tools.autocompact.resume import (
        _build_dropped_result_stubs,
        _resume_via_llm,
    )
    from gptme.util.tokens import len_tokens

    messages = [Message("system", "system prompt"), Message("user", "run tools")]
    for i in range(30):
        messages.append(Message("assistant", f"```shell\necho {i}\n```"))
        messages.append(Message("system", f"out-{i}", call_id=f"call-{i}"))

    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()

    stub = _build_dropped_result_stubs(manager, retained=[messages[0]], model="gpt-4")
    assert stub is not None
    catalog_tokens = len_tokens([stub], model="gpt-4")
    assert catalog_tokens > 80

    checkpoint = Message("assistant", "## Objective\nContinue.\n" + ("step " * 40))
    without_catalog = (
        len_tokens([messages[0]], model="gpt-4")
        + 80  # intro message allowance
        + len_tokens([checkpoint], model="gpt-4")
    )
    budget = without_catalog + catalog_tokens // 3
    assert without_catalog < budget < without_catalog + catalog_tokens

    fake_model = SimpleNamespace(
        model="gpt-4", context=10_000, max_output=100, full="gpt-4"
    )
    monkeypatch.setattr(
        "gptme.tools.autocompact.resume.get_default_model", lambda: fake_model
    )
    monkeypatch.setattr(
        "gptme.tools.autocompact.resume.get_context_budget",
        lambda *args, **kwargs: budget,
    )

    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=True,
            keep_recent_tokens=0,
            checkpoint_response=checkpoint,
        )
    )
    total = len_tokens(manager.log.messages, model="gpt-4")
    assert total <= budget, f"Compacted view exceeds budget: {total} > {budget}"


def test_checkpoint_final_response_after_declined_call_is_recognized(monkeypatch):
    from gptme.tools.autocompact.hook import _pending_checkpoint_turn

    monkeypatch.setattr("gptme.tools.base.get_tool_format", lambda: "tool")
    request = Message(
        "user", "Create checkpoint", metadata={"compaction_checkpoint_view": ""}
    )
    final = Message("assistant", "## Objective\nContinue building.")
    messages = [
        request,
        Message("assistant", '@missing_tool(call-1): {"arg": "x"}'),
        Message("system", "Tool declined", call_id="call-1"),
        final,
    ]
    assert _pending_checkpoint_turn(messages, "") == (0, final)


def test_manual_compaction_on_branch_preserves_catalog_source(tmp_path):
    from gptme.tools.autocompact.resume import _resume_via_llm
    from gptme.tools.recall import recall_result

    messages = _messages()
    logdir = tmp_path / "conversation"
    manager = LogManager(messages, logdir=logdir, lock=False)
    manager.write()
    manager.branch("main")
    manager.branch("experiment")
    manager.log = type(manager.log)(
        [
            messages[0],
            Message("user", "Try another tool"),
            Message("assistant", "different call"),
            Message("system", "different result", call_id="call-exp"),
        ]
    )
    list(
        _resume_via_llm(
            manager,
            list(manager.log.messages),
            use_view_branch=False,
            keep_recent_tokens=0,
            checkpoint_response=Message("assistant", "## Objective\nManual compact."),
        )
    )

    assert any("[result #4," in m.content for m in manager.log.messages)
    assert not any("[result #3," in m.content for m in manager.log.messages)
    assert manager.master_log.messages == messages
    assert "different result" in recall_result(4)
    manager.switch_to_master()
    assert "different result" in recall_result(4)
    manager.write()
    reloaded = LogManager.load(logdir, branch="experiment", lock=False)
    assert "different result" in recall_result(4)
    assert reloaded.master_log.messages == messages


def test_oversized_checkpoint_drops_catalog_before_truncating(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from gptme.tools.autocompact.resume import _resume_via_llm
    from gptme.util.tokens import len_tokens

    messages = [Message("system", "system prompt")]
    for i in range(50):
        messages.extend(
            [
                Message("assistant", f"old tool call {i}"),
                Message("system", f"result {i}", call_id=f"call-{i}"),
            ]
        )
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    checkpoint = Message(
        "assistant", "## Objective\nIMPORTANT SAVED WORK\n" + "step " * 1000
    )
    budget = 200
    monkeypatch.setattr(
        "gptme.tools.autocompact.resume.get_default_model",
        lambda: SimpleNamespace(
            model="gpt-4", context=10_000, max_output=100, full="gpt-4"
        ),
    )
    monkeypatch.setattr(
        "gptme.tools.autocompact.resume.get_context_budget",
        lambda *args, **kwargs: budget,
    )
    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=True,
            keep_recent_tokens=0,
            checkpoint_response=checkpoint,
        )
    )
    assert any("IMPORTANT SAVED WORK" in m.content for m in manager.log.messages)
    assert any("checkpoint truncated" in m.content for m in manager.log.messages)
    assert len_tokens(manager.log.messages, model="gpt-4") <= budget


def test_manual_compaction_recall_survives_reload(tmp_path):
    from gptme.tools.autocompact.resume import _resume_via_llm
    from gptme.tools.recall import recall_result

    messages = _messages()
    logdir = tmp_path / "conversation"
    manager = LogManager(messages, logdir=logdir, lock=False)
    manager.write()
    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=False,
            keep_recent_tokens=0,
            checkpoint_response=Message("assistant", "## Objective\nManual compact."),
        )
    )
    assert (logdir / "lossless.jsonl").is_file()
    reloaded = LogManager.load(logdir, lock=False)
    assert reloaded.master_log.messages == messages
    assert "0123456789" in recall_result(3)
    assert "recent result" in recall_result(5)


def test_native_recall_schema_exposes_result_and_paging_arguments():
    from gptme.llm.llm_anthropic import _spec2tool as anthropic_tool
    from gptme.llm.llm_openai import _spec2tool as openai_tool
    from gptme.llm.models import get_model
    from gptme.tools.recall import tool

    schemas = [
        anthropic_tool(tool)["input_schema"],
        openai_tool(tool, get_model("openai/gpt-4o-mini"))["function"]["parameters"],
    ]
    for schema in schemas:
        properties = schema["properties"]
        assert isinstance(properties, dict)
        assert set(properties) == {"result_id", "start_char", "max_chars"}
        assert schema["required"] == ["result_id"]
        assert all(p["type"] == "integer" for p in properties.values())


def test_result_catalog_explains_when_recall_is_not_loaded(tmp_path, monkeypatch):
    from gptme.tools.autocompact.resume import _build_dropped_result_stubs

    monkeypatch.setattr("gptme.tools._get_loaded_tools", lambda: [])
    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    stub = _build_dropped_result_stubs(manager, [messages[0]], "gpt-4")

    assert stub is not None
    assert "[result #3," in stub.content
    assert "recall_result(" not in stub.content
    assert "not loaded" in stub.content
    assert "/tools load recall" in stub.content


def test_hidden_non_tool_message_is_not_recallable(tmp_path):
    from gptme.tools.autocompact.resume import _build_dropped_result_stubs
    from gptme.tools.recall import recall_result

    messages = _messages() + [Message("system", "hidden notification", hide=True)]
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    stub = _build_dropped_result_stubs(manager, [messages[0]], "gpt-4")

    assert stub is not None
    assert "[result #6," not in stub.content
    assert "not a tool result" in recall_result(6)


def test_user_lossless_branch_does_not_replace_saved_history(tmp_path):
    """A user branch named lossless must survive compaction without owning recall."""
    from gptme.tools.autocompact.resume import _resume_via_llm
    from gptme.tools.recall import recall_result

    messages = _messages()
    logdir = tmp_path / "conversation"
    manager = LogManager(messages, logdir=logdir, lock=False)
    manager.write()
    manager.branch("lossless")
    experiment = [
        messages[0],
        Message("assistant", "experiment call"),
        Message("system", "experiment output", call_id="call-exp"),
    ]
    manager.log = type(manager.log)(experiment)
    manager.write()

    # Include branches already present on disk, not only freshly created ones.
    manager = LogManager.load(logdir, lock=False)
    assert manager.master_log.messages == messages
    list(
        _resume_via_llm(
            manager,
            list(manager.log.messages),
            use_view_branch=False,
            keep_recent_tokens=0,
            checkpoint_response=Message("assistant", "## Objective\nManual compact."),
        )
    )
    assert "0123456789" in recall_result(3)
    manager.append(Message("assistant", "later call"))
    manager.append(Message("system", "later main result", call_id="call-later"))
    result_id = len(manager.master_log.messages)
    manager.write()

    reloaded = LogManager.load(logdir, lock=False)
    assert "0123456789" in recall_result(3)
    assert "later main result" in recall_result(result_id)
    reloaded.branch("lossless")
    assert reloaded.log.messages == experiment
    reloaded.append(Message("system", "branch-only result", call_id="call-branch"))
    assert "branch-only result" not in [m.content for m in reloaded.master_log.messages]
    reloaded.switch_to_master()
    reloaded.write()
    assert "branch-only result" not in [
        m.content for m in LogManager.load(logdir, lock=False).master_log.messages
    ]


def test_failed_lossless_write_keeps_original_transcript(tmp_path, monkeypatch):
    """A failed snapshot must not leave only the compacted checkpoint on disk."""
    import errno

    import pytest

    from gptme.logmanager import Log
    from gptme.tools.autocompact.resume import _resume_via_llm
    from gptme.tools.recall import recall_result

    messages = _messages()
    logdir = tmp_path / "conversation"
    manager = LogManager(messages, logdir=logdir, lock=False)
    manager.write()
    original_bytes = (logdir / "conversation.jsonl").read_bytes()
    write_jsonl = Log.write_jsonl

    def fail_snapshot(self, output, append=False):
        if output.name == "lossless.jsonl":
            raise OSError(errno.ENOSPC, "No space left on device")
        return write_jsonl(self, output, append=append)

    with monkeypatch.context() as patch:
        patch.setattr(Log, "write_jsonl", fail_snapshot)
        with pytest.raises(OSError, match="No space left"):
            list(
                _resume_via_llm(
                    manager,
                    messages,
                    use_view_branch=False,
                    keep_recent_tokens=0,
                    checkpoint_response=Message("assistant", "## Objective\nCompact."),
                )
            )

    assert (logdir / "conversation.jsonl").read_bytes() == original_bytes
    reloaded = LogManager.load(logdir, lock=False)
    assert reloaded.master_log.messages == messages
    assert "0123456789" in recall_result(3)


def test_quiet_markdown_tool_result_remains_recallable(tmp_path):
    """Suppressing terminal printing does not remove an output from recall."""
    from gptme.tools.autocompact.resume import _build_dropped_result_stubs
    from gptme.tools.recall import recall_result

    messages = [
        Message("system", "system prompt"),
        Message("assistant", "```shell\necho saved\n```"),
        Message("system", "quiet saved output", quiet=True),
    ]
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    stub = _build_dropped_result_stubs(manager, [messages[0]], "gpt-4")

    assert stub is not None
    assert "[result #3," in stub.content
    assert "quiet saved output" in recall_result(3)


def test_compacted_branch_catalog_and_recall_use_owned_archive(tmp_path):
    from gptme.logmanager import Log
    from gptme.tools.autocompact.resume import _build_dropped_result_stubs
    from gptme.tools.recall import recall_result

    manager = LogManager(_messages(), logdir=tmp_path / "branch-recall", lock=False)
    manager.preserve_lossless_log()
    manager.log = Log([Message("system", "Summary")])
    manager.branch("alternative")
    manager.append(Message("assistant", "branch call"))
    manager.append(Message("system", "branch result", call_id="branch"))
    manager.preserve_lossless_log()
    manager.log = Log([Message("system", "Branch summary")])
    manager.write()
    manager.branch("main")
    manager.append(Message("assistant", "main call"))
    manager.append(Message("system", "divergent main result", call_id="main"))
    resumed = LogManager.load(manager.logdir, branch="alternative", lock=False)
    assert "branch result" in recall_result(7)
    assert "divergent main result" not in recall_result(7)
    stub = _build_dropped_result_stubs(resumed, retained=[], model="gpt-4")
    assert stub is not None
    assert "[result #7," in stub.content
    assert "[result #3," in stub.content


def test_compacted_branch_edit_undo_preserves_result_positions(tmp_path):
    from gptme.logmanager import Log
    from gptme.tools.autocompact.resume import _build_dropped_result_stubs
    from gptme.tools.recall import recall_result

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "stable-branch", lock=False)
    manager.preserve_lossless_log()
    catalog = _build_dropped_result_stubs(manager, [messages[0]], "gpt-4")
    assert catalog is not None
    manager.log = Log([messages[0], Message("system", "Summary"), catalog])
    manager.branch("alternative")
    manager.append(Message("user", "Undo this"))
    manager.undo(1, quiet=True)
    assert "0123456789" in recall_result(3)
    assert "recent result" in recall_result(5)
    # A no-op edit through the editor serializes and reconstructs messages.
    from gptme.message import msgs_to_toml, toml_to_msgs

    manager.edit(toml_to_msgs(msgs_to_toml(manager.log)))
    resumed = LogManager.load(manager.logdir, branch="alternative", lock=False)
    assert "0123456789" in recall_result(3)
    assert "recent result" in recall_result(5)
    assert [m.content for m in resumed.full_log][:5] == [m.content for m in messages]


def test_branch_view_edit_keeps_later_recall_ids_after_removal(tmp_path):
    from gptme.tools.recall import recall_result

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "view-edit", lock=False)
    manager.branch("alternative")  # No manual-compaction archive yet.
    manager.create_view("compacted-001", messages)
    manager.switch_view("compacted-001")
    manager.edit([messages[0], *messages[3:]])
    assert "recent result" in recall_result(5)
    assert "not a tool result" in recall_result(3)
    manager.switch_to_master()
    assert manager.log.messages == [messages[0], *messages[3:]]
    LogManager.load(manager.logdir, branch="alternative", lock=False)
    assert "recent result" in recall_result(5)
    assert "not a tool result" in recall_result(3)


def test_compacted_branch_reorder_is_rejected_without_changing_history(tmp_path):
    import pytest

    from gptme.logmanager import Log

    messages = _messages()
    manager = LogManager(messages, logdir=tmp_path / "reorder", lock=False)
    manager.preserve_lossless_log()
    manager.log = Log(messages)
    manager.branch("alternative")
    before = manager.full_log.messages[:]
    with pytest.raises(ValueError, match="Cannot reorder or insert"):
        manager.edit([messages[0], *messages[3:], *messages[1:3]])
    assert manager.full_log.messages == before
    assert manager.log.messages == messages
    with pytest.raises(ValueError, match="Cannot reorder or insert"):
        manager.edit([messages[0], Message("user", "Inserted"), *messages[1:]])
    assert manager.full_log.messages == before
    assert manager.log.messages == messages


def test_branch_edit_distinguishes_results_with_equal_timestamps(tmp_path):
    from gptme.logmanager import Log
    from gptme.tools.recall import recall_result

    first = Message("system", "first", call_id="a")
    second = Message("system", "second", call_id="b", timestamp=first.timestamp)
    manager = LogManager([first, second], logdir=tmp_path / "equal-time", lock=False)
    manager.preserve_lossless_log()
    manager.log = Log([first, second])
    manager.branch("alternative")
    manager.edit([second])
    assert "second" in recall_result(2)
    assert "not a tool result" in recall_result(1)
    LogManager.load(manager.logdir, branch="alternative", lock=False)
    assert "second" in recall_result(2)


def test_current_branch_write_does_not_overwrite_other_branch_archive(tmp_path):
    from gptme.logmanager import Log
    from gptme.tools.recall import recall_result

    manager = LogManager(_messages(), logdir=tmp_path / "concurrent", lock=False)
    manager.preserve_lossless_log()
    manager.log = Log([Message("system", "Summary")])
    manager.branch("alternative")
    manager.write()
    stale = LogManager.load(manager.logdir, lock=False)
    writer = LogManager.load(manager.logdir, branch="alternative", lock=False)
    writer.append(Message("system", "new branch output", call_id="new"))
    stale.write(branches=False)
    LogManager.load(manager.logdir, branch="alternative", lock=False)
    assert "new branch output" in recall_result(6)


def test_append_does_not_overwrite_unchanged_sibling_archive(tmp_path):
    from gptme.logmanager import Log
    from gptme.tools.recall import recall_result

    manager = LogManager(_messages(), logdir=tmp_path / "stale-append", lock=False)
    manager.preserve_lossless_log()
    manager.log = Log([Message("system", "Summary")])
    manager.branch("alternative")
    manager.write()
    stale = LogManager.load(manager.logdir, lock=False)
    writer = LogManager.load(manager.logdir, branch="alternative", lock=False)
    writer.append(Message("system", "new branch output", call_id="new"))
    stale.append(Message("user", "Main-only addition"))
    LogManager.load(manager.logdir, branch="alternative", lock=False)
    assert "new branch output" in recall_result(6)


@pytest.mark.parametrize("branches", [False, True])
@pytest.mark.parametrize("fail_sync", [False, True])
def test_final_sync_includes_unchanged_branch_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branches: bool, fail_sync: bool
) -> None:
    from gptme.logmanager import Log

    manager = LogManager(_messages(), logdir=tmp_path / "final-sync", lock=False)
    manager.preserve_lossless_log()
    manager.log = Log([Message("system", "Summary")])
    manager.branch("alternative")
    manager.append(Message("system", "branch output", call_id="new"))
    archive_path = manager.logdir / "branches" / "alternative" / "lossless.jsonl"
    original = archive_path.read_bytes()
    archive_stat = archive_path.stat()
    fsync = os.fsync
    write_jsonl = Log.write_jsonl
    archive_synced = False

    def record_sync(fd: int) -> None:
        nonlocal archive_synced
        stat = os.fstat(fd)
        if (stat.st_dev, stat.st_ino) == (archive_stat.st_dev, archive_stat.st_ino):
            archive_synced = True
            if fail_sync:
                raise OSError("archive sync failed")
        fsync(fd)

    def no_archive_rewrite(self: Log, path: Path, *args, **kwargs) -> Log:
        assert path != archive_path, "unchanged archive must not be rewritten"
        return write_jsonl(self, path, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", record_sync)
    monkeypatch.setattr(Log, "write_jsonl", no_archive_rewrite)
    if fail_sync:
        with pytest.raises(OSError, match="archive sync failed"):
            manager.write(branches=branches, sync=True)
    else:
        manager.write(branches=branches, sync=True)
    assert archive_synced, "final durability barrier skipped the branch archive"
    assert archive_path.read_bytes() == original


def test_branch_edit_matches_changed_result_by_call_id_with_equal_timestamps(tmp_path):
    from gptme.logmanager import Log
    from gptme.tools.recall import recall_result

    first = Message("system", "first", call_id="a")
    second = Message("system", "second", call_id="b", timestamp=first.timestamp)
    manager = LogManager([first, second], logdir=tmp_path / "edited-time", lock=False)
    manager.preserve_lossless_log()
    manager.log = Log([first, second])
    manager.branch("alternative")
    manager.edit([second.replace(content="edited")])
    assert "edited" in recall_result(2)
    assert "not a tool result" in recall_result(1)


def test_reselect_current_branch_keeps_active_compacted_view(tmp_path):
    manager = LogManager(_messages(), logdir=tmp_path / "reselect", lock=False)
    manager.branch("alternative")
    summary = Message("system", "Keep this checkpoint")
    manager.create_view("compacted-001", [summary])
    manager.switch_view("compacted-001")
    manager.branch("alternative")
    assert manager.current_view == "compacted-001"
    assert manager.log.messages == [summary]
