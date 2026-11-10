"""Tests for the subagent ``yield`` checkpoint operation (gptme/gptme#4555).

No inference: the child is either simulated in-process or run as a real local
Python subprocess that drives the yield tool exactly as a session would.
"""

import json
import queue
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import gptme.tools.subagent.execution as subagent_execution
import gptme.tools.yield_checkpoint as yield_mod
from gptme.logmanager import Log
from gptme.message import Message
from gptme.tools.complete import SessionCompleteException
from gptme.tools.subagent.api import subagent_continue
from gptme.tools.subagent.execution import _monitor_subprocess
from gptme.tools.subagent.types import (
    ReturnType,
    Subagent,
    _completion_queue,
    _subagent_results,
    _subagent_results_lock,
    _subagents,
    _subagents_lock,
)
from gptme.tools.yield_checkpoint import (
    CHECKPOINT_FILENAME,
    execute_yield,
    load_checkpoint,
    yield_hook,
)

PAYLOAD = {
    "wait_for": "CI green on PR #1",
    "next_action": "merge PR #1",
    "artifacts": ["PR #1", "report.txt"],
}


def _yield_block(payload: dict = PAYLOAD) -> str:
    return f"```yield\n{json.dumps(payload)}\n```"


@pytest.fixture(autouse=True)
def _clean_registry():
    def clear():
        with _subagents_lock:
            _subagents.clear()
        with _subagent_results_lock:
            _subagent_results.clear()
        while True:
            try:
                _completion_queue.get_nowait()
            except queue.Empty:
                break

    clear()
    yield
    clear()


def _subagent(logdir: Path, agent_id: str = "yielder", **kwargs) -> Subagent:
    return Subagent(
        agent_id=agent_id,
        prompt="task",
        thread=None,
        logdir=logdir,
        model=None,
        **kwargs,
    )


def _write_log(logdir: Path, messages: list[Message]) -> None:
    logdir.mkdir(parents=True, exist_ok=True)
    Log(messages).write_jsonl(logdir / "conversation.jsonl")


def _commit(monkeypatch, logdir: Path, payload: dict = PAYLOAD) -> Message:
    monkeypatch.setattr(yield_mod, "_current_logdir", lambda: logdir)
    return execute_yield(json.dumps(payload), None, None)


class TestExecuteYield:
    def test_commits_checkpoint_atomically(self, tmp_path, monkeypatch):
        msg = _commit(monkeypatch, tmp_path)
        assert msg.content.startswith("Checkpoint committed:")
        record = load_checkpoint(tmp_path)
        assert record is not None
        assert record["wait_for"] == PAYLOAD["wait_for"]
        assert record["artifacts"] == PAYLOAD["artifacts"]
        assert record["conversation"] == tmp_path.name
        # No temp files left behind by the atomic write.
        assert [p.name for p in tmp_path.iterdir()] == [CHECKPOINT_FILENAME]

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            "[]",
            json.dumps({"next_action": "x"}),
            json.dumps({"wait_for": " ", "next_action": "x"}),
            json.dumps({"wait_for": "x", "next_action": "y", "artifacts": "PR"}),
            json.dumps({"wait_for": "x", "next_action": "y", "artifacts": [1]}),
        ],
    )
    def test_invalid_payload_commits_nothing(self, tmp_path, monkeypatch, content):
        monkeypatch.setattr(yield_mod, "_current_logdir", lambda: tmp_path)
        msg = execute_yield(content, None, None)
        assert msg.content.startswith("Checkpoint NOT committed:")
        assert not (tmp_path / CHECKPOINT_FILENAME).exists()

    def test_failed_write_keeps_previous_checkpoint(self, tmp_path, monkeypatch):
        _commit(monkeypatch, tmp_path)

        def fail(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(yield_mod.os, "replace", fail)
        msg = _commit(monkeypatch, tmp_path, {**PAYLOAD, "wait_for": "other"})
        assert msg.content.startswith("Checkpoint NOT committed:")
        record = load_checkpoint(tmp_path)
        assert record is not None and record["wait_for"] == PAYLOAD["wait_for"]
        assert [p.name for p in tmp_path.iterdir()] == [CHECKPOINT_FILENAME]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory barrier")
    @pytest.mark.parametrize("fail_directory_sync", [False, True])
    def test_syncs_directory_before_acknowledging(
        self, tmp_path, monkeypatch, fail_directory_sync
    ):
        import os
        import stat

        real_fsync = os.fsync
        events = []

        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                # The new name must be installed before its namespace barrier.
                record = load_checkpoint(tmp_path)
                assert record is not None
                assert record["wait_for"] == PAYLOAD["wait_for"]
                events.append("directory")
                if fail_directory_sync:
                    raise OSError("directory sync failed")
            else:
                events.append("file")
            real_fsync(fd)

        monkeypatch.setattr(yield_mod.os, "fsync", fsync)
        msg = _commit(monkeypatch, tmp_path)
        assert events == ["file", "directory"]
        assert msg.content.startswith(
            "Checkpoint NOT committed:"
            if fail_directory_sync
            else "Checkpoint committed:"
        )
        if fail_directory_sync:
            messages = [Message("assistant", _yield_block()), msg]
            assert list(yield_hook(messages)) == []
            monkeypatch.setattr(yield_mod.os, "fsync", real_fsync)
            _write_log(tmp_path, [Message("user", "task"), *messages])
            assert _subagent(tmp_path).status().status == "failure"
        assert not list(tmp_path.glob("*.tmp"))

    def test_without_conversation_dir(self, monkeypatch):
        monkeypatch.setattr(yield_mod, "_current_logdir", lambda: None)
        msg = execute_yield(json.dumps(PAYLOAD), None, None)
        assert msg.content.startswith("Checkpoint NOT committed:")


class TestYieldHook:
    def test_stops_after_committed_yield(self, tmp_path, monkeypatch):
        committed = _commit(monkeypatch, tmp_path)
        messages = [
            Message("user", "task"),
            Message("assistant", _yield_block()),
            committed,
        ]
        with pytest.raises(SessionCompleteException):
            list(yield_hook(messages))

    def test_keeps_running_after_failed_yield(self):
        messages = [
            Message("user", "task"),
            Message("assistant", _yield_block()),
            Message("system", "Checkpoint NOT committed: write failed."),
        ]
        assert list(yield_hook(messages)) == []

    @pytest.mark.parametrize("last_succeeds", [False, True])
    def test_last_yield_result_controls_stop(
        self, tmp_path, monkeypatch, last_succeeds
    ):
        monkeypatch.setattr(yield_mod, "_current_logdir", lambda: tmp_path)
        payloads = [json.dumps(PAYLOAD), "[]"]
        if last_succeeds:
            payloads.reverse()
        messages = [
            Message("assistant", "\n".join(f"```yield\n{p}\n```" for p in payloads)),
            *(execute_yield(p, None, None) for p in payloads),
            Message("system", "Other tool output after the yields."),
        ]
        if last_succeeds:
            with pytest.raises(SessionCompleteException):
                list(yield_hook(messages))
        else:
            assert list(yield_hook(messages)) == []

    def test_earlier_attempt_yield_does_not_stop_continuation(
        self, tmp_path, monkeypatch
    ):
        committed = _commit(monkeypatch, tmp_path)
        messages = [
            Message("assistant", _yield_block()),
            committed,
            Message("user", "CI is green, continue"),
            Message("assistant", "Merging now."),
        ]
        assert list(yield_hook(messages)) == []


class TestReadLogYield:
    @pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
    @pytest.mark.parametrize("committed", [False, True])
    def test_yield_formats_with_tool_unloaded(
        self, tmp_path, monkeypatch, tool_format, committed
    ):
        from gptme.tools.base import ToolUse

        call = ToolUse("yield", [], json.dumps(PAYLOAD))
        if tool_format == "tool":
            call = ToolUse(
                "yield",
                None,
                None,
                kwargs={"checkpoint": json.dumps(PAYLOAD)},
                call_id="yield-call",
            )
        message = Message(
            "assistant",
            call.to_output(tool_format).replace("@yield:", "@yield(yield-call):", 1),
        )
        # The parent need not load the child's disabled-by-default yield tool.
        monkeypatch.setattr("gptme.tools.get_tool_for_langtag", lambda lang: None)
        trailing = [_commit(monkeypatch, tmp_path)] if committed else []
        _write_log(tmp_path, [Message("user", "task"), message, *trailing])
        assert _subagent(tmp_path).status().status == (
            "blocked_external" if committed else "failure"
        )

    @pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
    def test_multiple_yields_use_last_executed_payload(
        self, tmp_path, monkeypatch, tool_format
    ):
        from gptme.tools.base import ToolUse

        other = {**PAYLOAD, "wait_for": "second condition"}
        calls = []
        for index, payload in enumerate((PAYLOAD, other)):
            call = ToolUse("yield", [], json.dumps(payload))
            if tool_format == "tool":
                call = ToolUse(
                    "yield",
                    None,
                    None,
                    kwargs={"checkpoint": json.dumps(payload)},
                )
            calls.append(
                call.to_output(tool_format).replace(
                    "@yield:", f"@yield(call-{index}):", 1
                )
            )
        message = Message("assistant", "\n".join(calls))
        first = _commit(monkeypatch, tmp_path)
        second = _commit(monkeypatch, tmp_path, other)
        _write_log(tmp_path, [Message("user", "task"), message, first, second])
        result = _subagent(tmp_path).status()
        assert result.status == "blocked_external"
        assert "second condition" in str(result.result)

    @pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
    @pytest.mark.parametrize("last_succeeds", [False, True])
    def test_last_write_result_controls_saved_outcome(
        self, tmp_path, monkeypatch, tool_format, last_succeeds
    ):
        from gptme.tools.base import ToolUse

        calls = []
        for index in range(2):
            call = ToolUse("yield", [], json.dumps(PAYLOAD))
            if tool_format == "tool":
                call = ToolUse(
                    "yield",
                    None,
                    None,
                    kwargs={"checkpoint": json.dumps(PAYLOAD)},
                )
            calls.append(
                call.to_output(tool_format).replace(
                    "@yield:", f"@yield(call-{index}):", 1
                )
            )
        message = Message("assistant", "\n".join(calls))
        replace = yield_mod.os.replace

        def fail(*args, **kwargs):
            raise OSError("disk full")

        receipts = []
        for succeeds in (not last_succeeds, last_succeeds):
            monkeypatch.setattr(yield_mod.os, "replace", replace if succeeds else fail)
            receipts.append(_commit(monkeypatch, tmp_path))
        monkeypatch.setattr(yield_mod.os, "replace", replace)
        messages = [
            Message("user", "task"),
            message,
            *receipts,
            Message("system", "Other tool output after the yields."),
        ]
        if last_succeeds:
            with pytest.raises(SessionCompleteException):
                list(yield_hook(messages))
        else:
            assert list(yield_hook(messages)) == []
        # A matching, fresh checkpoint survives even when the final write fails.
        assert load_checkpoint(tmp_path) is not None
        _write_log(tmp_path, messages)
        expected = "blocked_external" if last_succeeds else "failure"
        assert _subagent(tmp_path).status().status == expected
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        sa = _subagent(tmp_path, process=process, execution_mode="subprocess")
        _monitor_subprocess(sa)
        assert process.returncode == 0
        assert sa.status().status == expected
        _, _, status, *_ = _completion_queue.get_nowait()
        assert status == expected
        with _subagent_results_lock:
            _subagent_results.clear()
        assert _subagent(tmp_path).status().status == expected

    @pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
    @pytest.mark.parametrize("committed", [False, True])
    @pytest.mark.parametrize("completion_signal", ["call", "receipt", "both"])
    def test_completion_takes_precedence_over_yield(
        self, tmp_path, monkeypatch, tool_format, committed, completion_signal
    ):
        from gptme.tools.base import ToolUse
        from gptme.tools.complete import execute_complete

        calls = []
        for name, content in (("yield", json.dumps(PAYLOAD)), ("complete", "Done")):
            call = ToolUse(name, [], content)
            if tool_format == "tool":
                call = ToolUse(
                    name,
                    None,
                    None,
                    kwargs={"checkpoint": content} if name == "yield" else {},
                )
            calls.append(
                call.to_output(tool_format).replace(
                    f"@{name}:", f"@{name}({name}-call):", 1
                )
            )
        # Native providers can leave only prose plus the completion receipt.
        message = Message(
            "assistant", "Done" if completion_signal == "receipt" else "\n".join(calls)
        )
        receipt = (
            _commit(monkeypatch, tmp_path)
            if committed
            else Message("system", "Checkpoint NOT committed: disk full")
        )
        _write_log(
            tmp_path,
            [
                Message("user", "task"),
                message,
                receipt,
                *(
                    [execute_complete(None, None, None)]
                    if completion_signal != "call"
                    else []
                ),
                Message("system", "Unrelated trailing bookkeeping"),
            ],
        )
        assert _subagent(tmp_path).status().status == "success"
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        sa = _subagent(tmp_path, process=process, execution_mode="subprocess")
        _monitor_subprocess(sa)
        assert process.returncode == 0
        assert sa.status().status == "success"
        _, _, status, *_ = _completion_queue.get_nowait()
        assert status == "success"
        with _subagent_results_lock:
            _subagent_results.clear()
        assert _subagent(tmp_path).status().status == "success"

    @pytest.mark.parametrize("tool_format", ["markdown", "tool"])
    @pytest.mark.parametrize("xml_style", ["gptme", "haiku"])
    def test_xml_completion_example_does_not_mask_yield(
        self, tmp_path, monkeypatch, tool_format, xml_style
    ):
        from gptme.tools.base import ToolUse

        example = (
            "<tool-use><complete>Done</complete></tool-use>"
            if xml_style == "gptme"
            else '<function_calls><invoke name="complete">Done</invoke></function_calls>'
        )
        call = ToolUse("yield", [], json.dumps(PAYLOAD))
        if tool_format == "tool":
            call = ToolUse(
                "yield", None, None, kwargs={"checkpoint": json.dumps(PAYLOAD)}
            )
        message = Message(
            "assistant",
            f"Example only:\n```xml\n{example}\n```\n\n"
            + call.to_output(tool_format).replace("@yield:", "@yield(yield-call):", 1),
        )
        receipt = _commit(monkeypatch, tmp_path)
        _write_log(tmp_path, [Message("user", "task"), message, receipt])
        assert _subagent(tmp_path).status().status == "blocked_external"
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        sa = _subagent(tmp_path, process=process, execution_mode="subprocess")
        _monitor_subprocess(sa)
        assert sa.status().status == "blocked_external"
        _, _, status, *_ = _completion_queue.get_nowait()
        assert status == "blocked_external"
        with _subagent_results_lock:
            _subagent_results.clear()
        assert _subagent(tmp_path).status().status == "blocked_external"

    @pytest.mark.parametrize("xml_style", ["gptme", "haiku"])
    def test_real_xml_completion_after_identical_example(
        self, tmp_path, monkeypatch, xml_style
    ):
        call = (
            "<tool-use><complete>Done</complete></tool-use>"
            if xml_style == "gptme"
            else '<function_calls><invoke name="complete">Done</invoke></function_calls>'
        )
        message = Message("assistant", f"Example:\n```xml\n{call}\n```\n\n{call}")
        _write_log(tmp_path, [Message("user", "task"), message])
        result = _subagent(tmp_path).status()
        assert result.status == "success"
        assert result.result == f"Done\n\nFull log: {tmp_path}"

    @pytest.mark.parametrize("xml_style", ["gptme", "haiku"])
    @pytest.mark.parametrize("lxml_available", [False, True])
    @pytest.mark.parametrize("prose", ["", "Here is the result:\n"])
    @pytest.mark.parametrize("with_example", [False, True])
    def test_xml_completion_preserves_fenced_answer(
        self, tmp_path, monkeypatch, xml_style, lxml_available, prose, with_example
    ):
        import gptme.tools.base as base

        if lxml_available and not base._LXML_AVAILABLE:
            pytest.skip("lxml is not installed")
        monkeypatch.setattr(base, "_LXML_AVAILABLE", lxml_available)
        answer = prose + '```json\n{"result": "kept"}\n```'
        call = (
            f"<tool-use>\n<complete>\n{answer}\n</complete>\n</tool-use>"
            if xml_style == "gptme"
            else f'<function_calls>\n<invoke name="complete">\n{answer}\n</invoke>\n</function_calls>'
        )
        # An identical example before the real call must neither win nor cause
        # its fenced payload to be removed from the actual completion.
        content = (
            f"Example:\n````xml\n{call}\n````\n\n" if with_example else ""
        ) + call
        _write_log(tmp_path, [Message("user", "task"), Message("assistant", content)])
        result = _subagent(tmp_path).status()
        assert result.status == "success"
        assert result.result == f"{answer}\n\nFull log: {tmp_path}"

    @pytest.mark.parametrize("xml_style", ["gptme", "haiku"])
    @pytest.mark.parametrize("lxml_available", [False, True])
    @pytest.mark.parametrize("with_example", [False, True])
    def test_xml_completion_preserves_nested_wrapper_in_fenced_answer(
        self, tmp_path, monkeypatch, xml_style, lxml_available, with_example
    ):
        import gptme.tools.base as base

        if lxml_available and not base._LXML_AVAILABLE:
            pytest.skip("lxml is not installed")
        monkeypatch.setattr(base, "_LXML_AVAILABLE", lxml_available)
        example = (
            "<tool-use><shell>echo hi</shell></tool-use>"
            if xml_style == "gptme"
            else '<function_calls><invoke name="shell">echo hi</invoke></function_calls>'
        )
        answer = f"Example:\n```xml\n{example}\n<!-- literal & text -->\n```\nReport after inner wrapper: kept."
        call = (
            f"<tool-use><complete>\n{answer}\n</complete></tool-use>"
            if xml_style == "gptme"
            else f'<function_calls><invoke name="complete">\n{answer}\n</invoke></function_calls>'
        )
        content = (f"````xml\n{call}\n````\n" if with_example else "") + call
        _write_log(tmp_path, [Message("user", "task"), Message("assistant", content)])
        result = _subagent(tmp_path).status()
        assert result.status == "success"
        assert result.result == f"{answer}\n\nFull log: {tmp_path}"
        parsed = list(yield_mod.iter_xml_signal_calls(content))
        assert [tool.tool for tool in parsed] == ["complete"]
        assert parsed[0].start == content.rfind(
            "<complete>" if xml_style == "gptme" else '<invoke name="complete">'
        )

    @pytest.mark.parametrize("xml_style", ["gptme", "haiku"])
    def test_xml_signal_offsets_after_escaped_fences(self, xml_style):
        answer = "```xml\n<tool-use><complete>example & only</complete></tool-use>\n```"
        if xml_style == "gptme":
            content = f"<tool-use><complete>\n{answer}\n</complete><yield>{{}}</yield></tool-use>"
            expected_offset = content.index("<yield>")
        else:
            content = f'<function_calls><invoke name="complete">\n{answer}\n</invoke><invoke name="yield">{{}}</invoke></function_calls>'
            expected_offset = content.index('<invoke name="yield">')
        parsed = list(yield_mod.iter_xml_signal_calls(content))
        assert [tool.tool for tool in parsed] == ["complete", "yield"]
        assert parsed[0].content == answer
        assert parsed[1].start == expected_offset

    @pytest.mark.parametrize("xml_style", ["gptme", "haiku"])
    @pytest.mark.parametrize("lxml_available", [False, True])
    @pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
    def test_xml_yield_example_does_not_replace_real_payload(
        self, tmp_path, monkeypatch, xml_style, lxml_available, tool_format
    ):
        import gptme.tools.base as base

        if lxml_available and not base._LXML_AVAILABLE:
            pytest.skip("lxml is not installed")
        monkeypatch.setattr(base, "_LXML_AVAILABLE", lxml_available)
        example_payload = json.dumps({**PAYLOAD, "wait_for": "example only"})
        example = (
            f"<tool-use><yield>{example_payload}</yield></tool-use>"
            if xml_style == "gptme"
            else f'<function_calls><invoke name="yield">{example_payload}</invoke></function_calls>'
        )
        call = base.ToolUse("yield", [], json.dumps(PAYLOAD))
        if tool_format == "tool":
            call = base.ToolUse(
                "yield", None, None, kwargs={"checkpoint": json.dumps(PAYLOAD)}
            )
        message = Message(
            "assistant",
            call.to_output(tool_format).replace("@yield:", "@yield(real):", 1)
            + f"\n\nExample only:\n```xml\n{example}\n```",
        )
        receipt = _commit(monkeypatch, tmp_path)
        assert yield_mod.find_yield_block(message.content) == json.dumps(PAYLOAD)
        with pytest.raises(SessionCompleteException):
            list(yield_hook([message, receipt]))
        _write_log(tmp_path, [Message("user", "task"), message, receipt])
        result = _subagent(tmp_path).status()
        assert result.status == "blocked_external"
        assert str(PAYLOAD["wait_for"]) in str(result.result)

    def test_committed_yield_reports_blocked_external(self, tmp_path, monkeypatch):
        block = Message("assistant", _yield_block())
        committed = _commit(monkeypatch, tmp_path)
        _write_log(tmp_path, [Message("user", "task"), block, committed])

        result = _subagent(tmp_path).status()

        assert result.status == "blocked_external"
        assert isinstance(result.result, str)
        assert "CI green on PR #1" in result.result
        assert "merge PR #1" in result.result
        assert str(tmp_path / CHECKPOINT_FILENAME) in result.result

    def test_uncommitted_yield_is_not_resumable(self, tmp_path):
        _write_log(
            tmp_path, [Message("user", "task"), Message("assistant", _yield_block())]
        )
        result = _subagent(tmp_path).status()
        assert result.status == "failure"
        assert "not committed" in str(result.result)

    def test_non_finite_checkpoint_time_is_rejected(self, tmp_path, monkeypatch):
        _commit(monkeypatch, tmp_path)
        path = tmp_path / CHECKPOINT_FILENAME
        record = json.loads(path.read_text())
        record["created_at"] = float("inf")
        path.write_text(json.dumps(record))
        assert load_checkpoint(tmp_path) is None

    @pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
    def test_mismatched_checkpoint_is_not_resumable(
        self, tmp_path, monkeypatch, tool_format
    ):
        from gptme.tools.base import ToolUse

        call = ToolUse("yield", [], json.dumps(PAYLOAD))
        if tool_format == "tool":
            call = ToolUse(
                "yield",
                None,
                None,
                kwargs={"checkpoint": json.dumps(PAYLOAD)},
                call_id="mismatch",
            )
        message = Message(
            "assistant",
            call.to_output(tool_format).replace("@yield:", "@yield(mismatch):", 1),
        )
        receipt = _commit(monkeypatch, tmp_path, {**PAYLOAD, "wait_for": "other"})
        _write_log(
            tmp_path,
            [Message("user", "task"), message, receipt],
        )
        assert _subagent(tmp_path).status().status == "failure"

    def test_checkpoint_from_earlier_attempt_is_not_resumable(
        self, tmp_path, monkeypatch
    ):
        receipt = _commit(monkeypatch, tmp_path)
        later = Message(
            "assistant",
            _yield_block(),
            timestamp=datetime.now() + timedelta(hours=1),  # noqa: DTZ005
        )
        _write_log(tmp_path, [Message("user", "task"), later, receipt])
        assert _subagent(tmp_path).status().status == "failure"

    def test_native_tool_call_yield(self, tmp_path, monkeypatch):
        """A native call leaves no yield block; the commit receipt identifies it."""
        call = Message("assistant", "Waiting on CI.")
        monkeypatch.setattr(yield_mod, "_current_logdir", lambda: tmp_path)
        receipt = execute_yield(None, None, {"checkpoint": json.dumps(PAYLOAD)})
        assert receipt.content.startswith("Checkpoint committed:")
        _write_log(tmp_path, [Message("user", "task"), call, receipt])
        with pytest.raises(SessionCompleteException):
            list(yield_hook([Message("user", "task"), call, receipt]))
        assert _subagent(tmp_path).status().status == "blocked_external"


@pytest.mark.parametrize("tool_format", ["markdown", "xml", "tool"])
def test_failed_yield_exit_zero_remains_failure(tmp_path, tool_format):
    from gptme.tools.base import ToolUse

    call = ToolUse("yield", [], json.dumps(PAYLOAD))
    if tool_format == "tool":
        call = ToolUse(
            "yield",
            None,
            None,
            kwargs={"checkpoint": json.dumps(PAYLOAD)},
            call_id="failed-yield",
        )
    _write_log(
        tmp_path,
        [
            Message("user", "task"),
            Message(
                "assistant",
                call.to_output(tool_format).replace(
                    "@yield:", "@yield(failed-yield):", 1
                ),
            ),
            Message("system", "Checkpoint NOT committed: disk full"),
        ],
    )
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    sa = _subagent(tmp_path, process=process, execution_mode="subprocess")
    _monitor_subprocess(sa)
    assert process.returncode == 0
    assert sa.status().status == "failure"
    _, _, status, *_ = _completion_queue.get_nowait()
    assert status == "failure"
    with _subagent_results_lock:
        _subagent_results.clear()
    assert _subagent(tmp_path).status().status == "failure"


CHILD = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path

    import gptme.tools.yield_checkpoint as y
    from gptme.logmanager import Log
    from gptme.message import Message
    from gptme.tools.complete import SessionCompleteException

    logdir, workspace = Path(sys.argv[1]), Path(sys.argv[2])
    payload = json.loads(sys.argv[3])

    # The attempt's real work: publish an artifact once.
    (workspace / "report.txt").write_text("published\\n")

    messages = [Message("user", "write the report, then wait for CI")]
    messages.append(Message("assistant", "```yield\\n" + json.dumps(payload) + "\\n```"))
    y._current_logdir = lambda: logdir
    messages.append(y.execute_yield(json.dumps(payload), None, None))
    Log(messages).write_jsonl(logdir / "conversation.jsonl")
    try:
        list(y.yield_hook(messages))
    except SessionCompleteException:
        sys.exit(0)
    sys.exit(3)  # the session would have kept running
    """
)


CONTINUATION_CHILD = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from gptme.logmanager import Log
    from gptme.message import Message

    logdir, workspace, prompt = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    log = Log.read_jsonl(logdir / "conversation.jsonl")
    # Same conversation: the previous attempt's yield is in this child's history.
    if not any("```yield" in m.content for m in log.messages):
        sys.exit(4)
    # The recorded artifact is reused, not republished.
    if (workspace / "report.txt").read_text() != "published\\n":
        sys.exit(5)
    messages = list(log.messages)
    messages.append(Message("user", prompt))
    messages.append(Message("assistant", "```complete\\nmerged PR #1\\n```"))
    Log(messages).write_jsonl(logdir / "conversation.jsonl")
    """
)


def test_real_subprocess_yield_survives_parent_restart(tmp_path, monkeypatch):
    """Artifact → yield → blocked_external → restart → continue the same conversation."""
    logdir = tmp_path / "conv"
    workspace = tmp_path / "workspace"
    logdir.mkdir()
    workspace.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(logdir), str(workspace), json.dumps(PAYLOAD)]
    )
    sa = _subagent(logdir, process=process, execution_mode="subprocess")
    with _subagents_lock:
        _subagents.append(sa)

    _monitor_subprocess(sa)

    assert process.returncode == 0
    with _subagent_results_lock:
        result = _subagent_results["yielder"]
    assert result.status == "blocked_external"
    _parent_logdir, agent_id, status, summary, *_ = _completion_queue.get_nowait()
    assert (agent_id, status) == ("yielder", "blocked_external")
    assert "CI green on PR #1" in summary
    # The caller-managed workspace and its artifact are untouched.
    assert (workspace / "report.txt").read_text() == "published\n"

    # Parent restart: in-memory results are gone; the verdict is re-derived from
    # the conversation plus the durable checkpoint, not from cached state.
    with _subagent_results_lock:
        _subagent_results.clear()
    restarted = _subagent(logdir, execution_mode="subprocess")
    after = restarted.status()
    assert after.status == "blocked_external"
    assert "merge PR #1" in str(after.result)

    # Continue the same conversation once the (local) condition is settled.
    artifact_mtime = (workspace / "report.txt").stat().st_mtime_ns
    with _subagents_lock:
        _subagents[:] = [restarted]
    launched: list[str] = []

    def run_child(prompt, logdir, resume, **kwargs):
        assert resume is True
        launched.append(prompt)
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                CONTINUATION_CHILD,
                str(logdir),
                str(workspace),
                prompt,
            ]
        )

    monkeypatch.setattr(subagent_execution, "_run_subagent_subprocess", run_child)
    subagent_continue("yielder", "CI is green, finish up")
    with _subagents_lock:
        continued = next(s for s in _subagents if s.agent_id == "yielder")
    assert continued.thread is not None
    continued.thread.join(timeout=30)
    assert not continued.thread.is_alive()

    assert launched == ["CI is green, finish up"]
    final = continued.status()
    assert final.status == "success", final.result
    assert "merged PR #1" in str(final.result)
    assert (workspace / "report.txt").stat().st_mtime_ns == artifact_mtime
    # Still one conversation: the yield is history, the completion is terminal.
    log = Log.read_jsonl(logdir / "conversation.jsonl")
    assert sum("```yield" in m.content for m in log.messages) == 1


TIMEOUT_CHILD = textwrap.dedent(
    """
    import json
    import sys
    import time
    from pathlib import Path

    import gptme.tools.yield_checkpoint as y

    logdir, payload = Path(sys.argv[1]), sys.argv[2]
    if payload:
        y.write_checkpoint(logdir, y.parse_checkpoint_payload(payload))
    (logdir / "ready").write_text("")
    time.sleep(60)  # never finishes: the parent's deadline must expire
    """
)


def _run_to_deadline(logdir: Path, payload: dict | None, **kwargs) -> ReturnType:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            TIMEOUT_CHILD,
            str(logdir),
            json.dumps(payload) if payload else "",
        ]
    )
    sa = _subagent(
        logdir, process=process, execution_mode="subprocess", timeout=2, **kwargs
    )
    with _subagents_lock:
        _subagents.append(sa)
    # The monitor's deadline starts when it begins waiting, so let the child
    # commit first: a slow interpreter start must not race the 2s deadline.
    deadline = time.monotonic() + 60
    while not (logdir / "ready").exists():
        assert process.poll() is None, "child exited before it was ready"
        assert time.monotonic() < deadline, "child never became ready"
        time.sleep(0.05)
    _monitor_subprocess(sa)
    assert process.returncode is not None
    with _subagent_results_lock:
        return _subagent_results["yielder"]


def test_real_subprocess_timeout_reports_checkpoint_from_this_attempt(tmp_path):
    """Deadline expiry stays ``timeout`` and names the checkpoint already on disk."""
    result = _run_to_deadline(tmp_path, PAYLOAD)

    assert result.status == "timeout"
    text = str(result.result)
    assert "Latest committed checkpoint (from this attempt)" in text
    assert "Waiting for: CI green on PR #1" in text
    assert "Next action: merge PR #1" in text
    assert "Artifacts: PR #1, report.txt" in text
    assert str(tmp_path / CHECKPOINT_FILENAME) in text


def test_real_subprocess_timeout_labels_earlier_attempt_checkpoint(
    tmp_path, monkeypatch
):
    """A prior attempt's checkpoint is reported as such, not as this attempt's yield."""
    _commit(monkeypatch, tmp_path)
    before = (tmp_path / CHECKPOINT_FILENAME).read_bytes()

    result = _run_to_deadline(tmp_path, None)

    assert result.status == "timeout"
    assert "Latest committed checkpoint (from an earlier attempt)" in str(result.result)
    # Expiry reports the record; it never rewrites it.
    assert (tmp_path / CHECKPOINT_FILENAME).read_bytes() == before


def test_real_subprocess_timeout_without_checkpoint_manufactures_none(tmp_path):
    result = _run_to_deadline(tmp_path, None)

    assert result.status == "timeout"
    assert "checkpoint" not in str(result.result).lower()
    assert load_checkpoint(tmp_path) is None
