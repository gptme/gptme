import builtins
import json
from contextlib import contextmanager
from io import TextIOWrapper
from pathlib import Path
from types import TracebackType
from unittest.mock import patch

import pytest

from gptme.dirs import get_logs_dir
from gptme.logmanager import (
    Log,
    LogManager,
    check_for_modifications,
    conversation_name_error,
)
from gptme.logmanager.manager import (
    _active_prompt_generation,
    _merge_consecutive_messages,
)
from gptme.message import Message
from gptme.tools import init_tools


@pytest.fixture(autouse=True)
def _init_tools():
    """Ensure tools are loaded for check_for_modifications tests."""
    init_tools(allowlist=["save", "patch", "append"])


def test_active_prompt_generation_preserves_conversation_history():
    """A replacement retires generated prompts, never user/assistant history."""
    old_prompt = Message("system", "old prompt", pinned=True, hide=True)
    user = Message("user", "question")
    assistant = Message("assistant", "answer")
    runtime = Message("system", "tool output")
    replacement = Message(
        "system",
        "new prompt",
        pinned=True,
        hide=True,
        metadata={"prompt_generation": "one"},
    )
    user_after = Message("user", "next question")

    result = _active_prompt_generation(
        [old_prompt, user, assistant, runtime, replacement, user_after]
    )

    assert result == [replacement, user, assistant, runtime, user_after]


def test_active_prompt_generation_preserves_later_pinned_system_messages():
    runtime_prompt = Message(
        "system",
        "runtime instruction",
        pinned=True,
        hide=True,
    )
    replacement = Message(
        "system",
        "complete replacement",
        pinned=True,
        hide=True,
        metadata={"prompt_generation": "replacement"},
    )
    user = Message("user", "history")

    result = _active_prompt_generation(
        [
            Message("system", "old core", pinned=True),
            user,
            runtime_prompt,
            replacement,
        ]
    )

    assert result == [replacement, user, runtime_prompt]


def test_active_prompt_generation_keeps_only_newest_replacement():
    generation_one = Message(
        "system",
        "generation one",
        pinned=True,
        metadata={"prompt_generation": "z-first"},
    )
    generation_two = Message(
        "system",
        "generation two",
        pinned=True,
        metadata={"prompt_generation": "a-second"},
    )
    user = Message("user", "history")

    result = _active_prompt_generation([generation_one, user, generation_two])

    assert result == [generation_two, user]


def test_load_snapshots_initial_message_files(tmp_path: Path):
    """Startup attachments must not be re-rendered from mutable live files."""
    from gptme.util.context import enrich_messages_with_context

    bootstrap_file = tmp_path / "bootstrap.md"
    bootstrap_file.write_text("original bootstrap")
    manager = LogManager.load(
        tmp_path / "conversation",
        initial_msgs=[Message("system", "bootstrap", files=[bootstrap_file])],
        create=True,
        lock=False,
    )

    first = enrich_messages_with_context(manager.log.messages, tmp_path)
    bootstrap_file.write_text("mutated bootstrap")
    second = enrich_messages_with_context(manager.log.messages, tmp_path)

    assert manager.log.messages[0].file_hashes[str(bootstrap_file)]
    assert first[0].content == second[0].content
    assert "original bootstrap" in second[0].content
    assert "mutated bootstrap" not in second[0].content


def test_constructor_snapshots_initial_message_files(tmp_path: Path):
    """Direct constructors used by server tasks must snapshot startup files."""
    bootstrap_file = tmp_path / "bootstrap.md"
    bootstrap_file.write_text("original bootstrap")

    manager = LogManager(
        [Message("system", "bootstrap", files=[bootstrap_file])],
        logdir=tmp_path / "conversation",
        lock=False,
    )

    assert manager.log.messages[0].file_hashes[str(bootstrap_file)]


def test_load_preserves_persistence_cursors(tmp_path: Path):
    """Loaded active, branch, and view logs continue with incremental appends."""
    logdir = tmp_path / "conversation"
    (logdir / "branches").mkdir(parents=True)
    (logdir / "views").mkdir()
    message = Message("user", "persisted")
    Log([message]).write_jsonl(logdir / "conversation.jsonl")
    Log([message]).write_jsonl(logdir / "branches" / "dev.jsonl")
    Log([message]).write_jsonl(logdir / "views" / "compact.jsonl")

    manager = LogManager.load(logdir, lock=False)

    assert manager._branches["main"].persisted_messages == 1
    assert (
        manager._branches["main"].persisted_path
        == (logdir / "conversation.jsonl").resolve()
    )
    assert (
        manager._branches["main"].persisted_size
        == (logdir / "conversation.jsonl").stat().st_size
    )
    assert manager._branches["dev"].persisted_messages == 1
    assert (
        manager._branches["dev"].persisted_path
        == (logdir / "branches" / "dev.jsonl").resolve()
    )
    assert (
        manager._branches["dev"].persisted_size
        == (logdir / "branches" / "dev.jsonl").stat().st_size
    )
    assert manager._views["compact"].persisted_messages == 1
    assert (
        manager._views["compact"].persisted_path
        == (logdir / "views" / "compact.jsonl").resolve()
    )
    assert (
        manager._views["compact"].persisted_size
        == (logdir / "views" / "compact.jsonl").stat().st_size
    )


def test_load_with_snapshotted_file_keeps_incremental_cursor(tmp_path: Path):
    """Preserving an existing attachment hash must keep message identity."""
    logdir = tmp_path / "conversation"
    attachment = tmp_path / "context.md"
    attachment.write_text("context")
    with patch("gptme.logmanager.manager.get_logs_dir", return_value=tmp_path):
        manager = LogManager(
            [Message("system", "context", files=[attachment])],
            logdir=logdir,
            lock=False,
        )
        manager.write()

        loaded = LogManager.load(logdir, lock=False)
        persisted_message = loaded.log.messages[0]
        assert loaded.log.persisted[0] is persisted_message
        loaded.append(Message("user", "next"))

    assert [
        message.content for message in Log.read_jsonl(logdir / "conversation.jsonl")
    ] == ["context", "next"]


def test_initial_message_directories_are_not_snapshotted(tmp_path: Path):
    """Directory prompt matches are context references, not file snapshots."""
    context_dir = tmp_path / "docs"
    context_dir.mkdir()

    manager = LogManager(
        [Message("system", "context", files=[context_dir])],
        logdir=tmp_path / "conversation",
        lock=False,
    )

    assert manager.log.messages[0].file_hashes == {}


def test_log_repr():
    """Log.__repr__ should have matched brackets."""
    log = Log([Message("user", "hello")])
    r = repr(log)
    assert r == "Log(messages=<1 msgs>)"
    assert "]" not in r


def test_branch():
    log = LogManager()

    # add message to main branch
    log.append(Message("assistant", "hello"))
    assert log.log[-1].content == "hello"

    # switch branch
    log.branch("dev")
    log.append(Message("assistant", "world"))
    assert log.log[-1].content == "world"
    assert log.log[-2].content == "hello"
    assert log.diff("main") == "+ Assistant: world"

    # switch back
    log.branch("main")
    assert log.log[-1].content == "hello"

    # check diff
    assert log.diff("dev") == "- Assistant: world"

    # undo and check no diff
    log.undo()
    assert log.diff("dev") == "- Assistant: hello\n- Assistant: world"

    d = log.to_dict(branches=True)
    assert "main" in d["branches"]
    assert "dev" in d["branches"]


def test_fork_rejects_path_traversal(tmp_path: Path, monkeypatch):
    """Fork names must stay within the logs directory."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    log = LogManager(logdir=get_logs_dir() / "seed")
    log.append(Message("user", "hello"))
    log.write()

    with pytest.raises(
        ValueError, match="conversation name must be a single path component"
    ):
        log.fork("../escape")

    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" leading", "conversation name cannot start or end with whitespace."),
        ("trailing ", "conversation name cannot start or end with whitespace."),
        ("foo\tbar", "conversation name cannot contain control characters."),
        ("foo\nbar", "conversation name cannot contain control characters."),
    ],
)
def test_conversation_name_error_rejects_control_and_edge_whitespace(
    value: str, expected: str
):
    assert conversation_name_error(value) == expected


def test_new_branch_persists_inherited_history(tmp_path: Path, monkeypatch):
    """A new branch writes its inherited prefix to its own destination."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    logdir = tmp_path / "logs" / "test-conv"
    manager = LogManager(logdir=logdir)
    manager.append(Message("user", "inherited"))

    manager.branch("dev")
    manager.append(Message("assistant", "branch-only"))

    persisted = Log.read_jsonl(logdir / "branches" / "dev.jsonl")
    assert [message.content for message in persisted] == ["inherited", "branch-only"]


def test_edit_backup_persists_complete_history(tmp_path: Path, monkeypatch):
    """An edit backup writes all messages despite originating from a persisted log."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    logdir = tmp_path / "logs" / "test-conv"
    manager = LogManager(logdir=logdir)
    manager.append(Message("user", "first"))
    manager.append(Message("assistant", "second"))

    manager.edit(Log([Message("user", "replacement")]))

    persisted = Log.read_jsonl(logdir / "branches" / "main-edit-0.jsonl")
    assert [message.content for message in persisted] == ["first", "second"]


def test_write_persists_main_branch_when_on_other_branch(tmp_path: Path, monkeypatch):
    """Regression test: writing while on a non-main branch should also persist
    the main branch to conversation.jsonl."""
    # Use tmp_path for logs dir so we don't write to the global logs directory
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    log = LogManager(logdir=tmp_path / "logs" / "test-conv")
    chat_id = log.chat_id

    # add message to main branch
    log.append(Message("assistant", "main message"))
    log.write()

    main_path = get_logs_dir() / chat_id / "conversation.jsonl"
    assert main_path.exists()
    main_content = main_path.read_text()
    assert "main message" in main_content

    # switch to dev branch and add a message
    log.branch("dev")
    log.append(Message("assistant", "dev message"))
    log.write()

    # main branch should still be written to conversation.jsonl
    main_content = main_path.read_text()
    assert "main message" in main_content

    # dev branch should be in branches/dev.jsonl
    dev_path = tmp_path / "logs" / "test-conv" / "branches" / "dev.jsonl"
    assert dev_path.exists()
    dev_content = dev_path.read_text()
    assert "dev message" in dev_content


def test_check_for_modifications_with_tool_use():
    """Test that check_for_modifications detects save/patch/append/morph tool uses."""
    log = Log(
        messages=[
            Message("user", "Please create a file"),
            Message(
                "assistant",
                "I'll create that file.\n```save test.py\nprint('hello')\n```",
            ),
        ]
    )
    assert check_for_modifications(log) is True


def test_check_for_modifications_no_tools():
    """Test that check_for_modifications returns False when no file tools are used."""
    log = Log(
        messages=[
            Message("user", "What is Python?"),
            Message("assistant", "Python is a programming language."),
        ]
    )
    assert check_for_modifications(log) is False


def test_check_for_modifications_beyond_third_message():
    """Test that modifications are detected even after 3+ assistant messages.

    Previously, only the first 3 messages were checked, which could miss
    modifications when the agent took many steps.
    """
    log = Log(
        messages=[
            Message("user", "Create a file"),
            Message("assistant", "Let me think about that..."),
            Message("assistant", "I need to check something first."),
            Message("assistant", "Almost ready..."),
            Message(
                "assistant",
                "Here it is.\n```save test.py\nprint('hello')\n```",
            ),
        ]
    )
    assert check_for_modifications(log) is True


def test_check_for_modifications_no_user_message():
    """Test that check_for_modifications returns False when no user message exists."""
    log = Log(
        messages=[
            Message("system", "System prompt"),
            Message("assistant", "```save test.py\nprint('hello')\n```"),
        ]
    )
    assert check_for_modifications(log) is False


def test_check_for_modifications_skips_system_messages():
    """Test that system messages between user and assistant are skipped."""
    log = Log(
        messages=[
            Message("user", "Create a file"),
            Message("system", "Tool output: success"),
            Message(
                "assistant",
                "Done.\n```save test.py\nprint('hello')\n```",
            ),
        ]
    )
    assert check_for_modifications(log) is True


def test_check_for_modifications_prevents_precommit_rerun_loop():
    """Test that only the LAST assistant message is checked to prevent infinite loops.

    When the agent responds to a pre-commit failure with text (no file modifications),
    check_for_modifications must return False to avoid re-triggering pre-commit.
    The original save is still visible in the log but should NOT cause a re-run.
    """
    log = Log(
        messages=[
            Message("user", "Create a file"),
            Message(
                "assistant",
                "Here it is.\n```save test.py\nprint('hello')\n```",
            ),
            Message("system", "Saved to test.py"),
            Message("system", "Pre-commit failed: E501 line too long"),
            Message("assistant", "I see the issue, let me fix the line length..."),
        ]
    )
    assert check_for_modifications(log) is False


def test_view_write_preserves_main_history(tmp_path: Path, monkeypatch):
    """Regression test: writing while on a compacted view must preserve the full
    main branch in conversation.jsonl, not overwrite it with compacted content."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    log = LogManager(logdir=tmp_path / "logs" / "test-conv")

    # Build a conversation with several messages
    log.append(Message("user", "first message"))
    log.append(Message("assistant", "first reply"))
    log.append(Message("user", "second message"))
    log.append(Message("assistant", "second reply"))

    # Create a compacted view with fewer messages
    compacted = Log([Message("system", "compacted summary")])
    log.create_view("compacted-001", compacted)
    log.switch_view("compacted-001")

    # Append a new message while on the view (triggers dual-write + write())
    log.append(Message("user", "new message after compact"))

    # Read conversation.jsonl — it should contain the FULL main branch history,
    # not the compacted view
    main_file = log.logfile
    persisted = Log.read_jsonl(main_file)
    contents = [m.content for m in persisted]
    assert "first message" in contents, "Main history lost after view write"
    assert "first reply" in contents, "Main history lost after view write"
    assert "second message" in contents, "Main history lost after view write"
    assert "new message after compact" in contents, "New message not in main"

    # The compacted view should be in views/
    view_file = tmp_path / "logs" / "test-conv" / "views" / "compacted-001.jsonl"
    assert view_file.exists()
    view_log = Log.read_jsonl(view_file)
    view_contents = [m.content for m in view_log]
    assert "compacted summary" in view_contents
    assert "new message after compact" in view_contents


def test_view_on_non_main_branch_persists_each_destination(tmp_path: Path, monkeypatch):
    """A view must not advance main's cursor against the current branch file."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    logdir = tmp_path / "logs" / "test-conv"
    manager = LogManager(logdir=logdir)
    manager.append(Message("user", "main history"))
    manager.branch("dev")
    manager.append(Message("assistant", "branch history"))
    manager.create_view("compacted-001", Log([Message("system", "summary")]))
    manager.switch_view("compacted-001")

    manager.append(Message("user", "view message"))

    main = Log.read_jsonl(logdir / "conversation.jsonl")
    branch = Log.read_jsonl(logdir / "branches" / "dev.jsonl")
    view = Log.read_jsonl(logdir / "views" / "compacted-001.jsonl")
    assert [message.content for message in main] == ["main history", "view message"]
    assert [message.content for message in branch] == [
        "main history",
        "branch history",
    ]
    assert [message.content for message in view] == ["summary", "view message"]


def test_view_log_setter_updates_view(tmp_path: Path, monkeypatch):
    """Regression test: the log setter should update the view when current_view
    is set, not silently update the branch."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    mgr = LogManager(logdir=tmp_path / "logs" / "test-conv")

    mgr.append(Message("user", "hello"))
    mgr.append(Message("assistant", "hi"))

    # Create and switch to a view
    view_log = Log([Message("system", "compacted")])
    mgr.create_view("compacted-001", view_log)
    mgr.switch_view("compacted-001")

    # Use the setter (as edit() and undo() do internally)
    new_view = Log([Message("system", "updated compacted")])
    mgr.log = new_view

    # The getter should return the updated view
    assert mgr.log[0].content == "updated compacted"
    # The view dict should be updated
    assert mgr._views["compacted-001"][0].content == "updated compacted"
    # The main branch should NOT be affected
    assert mgr._branches["main"][0].content == "hello"


def test_view_undo_works_on_view():
    """Test that undo() while on a view modifies the view, not the branch."""
    mgr = LogManager()
    mgr.append(Message("user", "hello"))
    mgr.append(Message("assistant", "hi"))

    # Create view with some messages
    view_log = Log(
        [
            Message("system", "summary"),
            Message("user", "follow-up"),
            Message("assistant", "response"),
        ]
    )
    mgr.create_view("compacted-001", view_log)
    mgr.switch_view("compacted-001")

    # Undo should remove from the view
    mgr.undo(quiet=True)
    assert len(mgr.log) == 2  # summary + follow-up
    assert mgr.log[-1].content == "follow-up"
    # Main branch should be unaffected
    assert len(mgr._branches["main"]) == 2  # hello + hi


def test_undo_more_than_log_length():
    """Regression: undo(n) where n > len(log) should not crash."""
    log = LogManager()
    log.append(Message("user", "hello"))
    log.append(Message("assistant", "world"))
    # undo more messages than exist — should stop gracefully, not IndexError
    log.undo(n=10, quiet=True)
    assert len(log.log) == 0


def test_undo_on_empty_log():
    """Regression: undo on empty log should print warning, not crash."""
    log = LogManager()
    # should return early with "Nothing to undo"
    log.undo(quiet=True)
    assert len(log.log) == 0


def test_undo_early_return_persists_to_disk(tmp_path: Path, monkeypatch):
    """Regression: undo() must persist when early-return path fires (log only had /undo command)."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    logdir = tmp_path / "logs" / "test-undo-early-return"
    log = LogManager(logdir=logdir)
    # A log whose only content is an /undo command message
    log.append(Message("user", "/undo"))
    assert len(log.log) == 1

    # undo() strips the /undo msg, sees empty log, and takes the early-return path
    log.undo(quiet=True)
    assert len(log.log) == 0

    # Reload from disk — the strip must survive
    reloaded = LogManager.load(logdir)
    assert len(reloaded.log) == 0


def test_undo_persists_to_disk(tmp_path: Path, monkeypatch):
    """Regression: undo() must persist changes to disk via write()."""
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    logdir = tmp_path / "logs" / "test-undo-persist"
    log = LogManager(logdir=logdir)
    log.append(Message("user", "hello"))
    log.append(Message("assistant", "world"))
    assert len(log.log) == 2

    log.undo(quiet=True)
    assert len(log.log) == 1

    # Reload from disk — undo must survive
    reloaded = LogManager.load(logdir)
    assert len(reloaded.log) == 1
    assert reloaded.log[-1].content == "hello"


def test_read_jsonl_malformed(tmp_path):
    """Test that malformed JSON lines are skipped gracefully."""
    jsonl_file = tmp_path / "test.jsonl"
    jsonl_file.write_text(
        '{"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:00Z"}\n'
        '{"role": "assistant", "content": "truncated stri\n'  # malformed
        "\n"  # empty line
        '{"role": "assistant", "content": "world", "timestamp": "2025-01-01T00:00:01Z"}\n'
    )
    log = Log.read_jsonl(jsonl_file)
    assert len(log.messages) == 2
    assert log.messages[0].content == "hello"
    assert log.messages[1].content == "world"


def test_merge_consecutive_does_not_merge_tool_result_messages():
    """Regression: _merge_consecutive_messages must NOT merge adjacent system
    messages that carry distinct call_ids.

    When an assistant turn contains two tool calls (call_A, call_B), gptme
    appends two system messages in sequence.  prune_ephemeral_messages() always
    calls _merge_consecutive_messages() before the next LLM request.  If that
    merge collapses the two messages into one (keeping only call_A via
    Message.concat()), the Codex/OpenAI Responses API receives a
    function_call(A) + function_call(B) but only a single
    function_call_output(A) and returns 400: "No tool output found for
    function call call_B".
    """
    msgs = [
        Message(role="system", content="result A", call_id="call_A"),
        Message(role="system", content="result B", call_id="call_B"),
    ]
    result = _merge_consecutive_messages(msgs)
    assert len(result) == 2, (
        f"Tool-result messages must NOT be merged; got {len(result)} message(s): {result}"
    )
    assert result[0].call_id == "call_A"
    assert result[1].call_id == "call_B"


def test_merge_consecutive_still_merges_plain_same_role_messages():
    """Non-tool-result adjacent system messages (no call_id) should still be
    merged as before — the fix must not regress the original pruning purpose."""
    msgs = [
        Message(role="system", content="part one"),
        Message(role="system", content="part two"),
    ]
    result = _merge_consecutive_messages(msgs)
    assert len(result) == 1
    assert "part one" in result[0].content
    assert "part two" in result[0].content


def test_merge_consecutive_preserves_prompt_cache_boundary():
    """Provider preprocessing must not merge volatile context into the prefix."""
    from gptme.prompts import SYSTEM_PROMPT_CACHE_BOUNDARY

    msgs = [
        Message(role="system", content="static"),
        Message(role="system", content=SYSTEM_PROMPT_CACHE_BOUNDARY),
        Message(role="system", content="dynamic"),
    ]

    assert _merge_consecutive_messages(msgs) == msgs


def test_read_jsonl_unknown_field(tmp_path):
    """Unknown message fields (e.g. from a newer gptme version) should be
    dropped rather than crashing the whole read with a TypeError."""
    jsonl_file = tmp_path / "test.jsonl"
    jsonl_file.write_text(
        '{"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:00Z", '
        '"some_future_field": 42}\n'
        '{"role": "assistant", "content": "world", "timestamp": "2025-01-01T00:00:01Z"}\n'
    )
    log = Log.read_jsonl(jsonl_file)
    # Both messages must survive; the unknown key is just ignored.
    assert len(log.messages) == 2
    assert log.messages[0].content == "hello"
    assert log.messages[1].content == "world"


# ── JSONL conversation I/O must be explicit UTF-8 ─────────────────────
#
# conversation.jsonl is the durability substrate: read on every startup,
# written on every turn, across machines and locales. gptme's own writers emit
# pure ASCII today (json.dumps defaults to ensure_ascii=True), but a log edited
# by hand, produced by an older/newer build, or written under any future
# ensure_ascii=False change puts real non-ASCII UTF-8 bytes on disk. Reading or
# writing those bytes without naming an encoding falls back to the platform's
# *preferred* encoding -- a legacy codepage on a stock Windows install -- so a
# conversation written on one machine can break on another. These tests pin the
# explicit UTF-8 discipline established for config (#3399) and tools (#2051) to
# the conversation-persistence layer, and mirror tests/test_config_encoding.py.


@contextmanager
def _legacy_default_encoding(codec: str):
    """Make encoding-less ``open()`` calls behave as under a legacy locale.

    Monkeypatching ``locale.getpreferredencoding`` does not work: CPython reads
    the locale encoding at the C level, so ``open()`` ignores the patched
    function. This shim instead supplies ``codec`` in exactly the position
    CPython would supply the locale's -- only when the caller passed no
    ``encoding`` -- so these tests fail on a machine of any locale when
    ``encoding=`` is missing, and pass on a machine of any locale when present.
    Mirrors the helper of the same name in tests/test_config_encoding.py.
    """
    real_open = builtins.open

    def shim(file, mode="r", *args, **kwargs):
        if "b" not in mode and kwargs.get("encoding") is None and len(args) < 2:
            kwargs["encoding"] = codec
        return real_open(file, mode, *args, **kwargs)

    with patch.object(builtins, "open", shim):
        yield


@contextmanager
def _record_open_calls():
    """Record every ``builtins.open`` call's file/mode/encoding for spy tests."""
    real_open = builtins.open
    calls: list[dict] = []

    def shim(file, mode="r", *args, **kwargs):
        calls.append(
            {"file": str(file), "mode": mode, "encoding": kwargs.get("encoding")}
        )
        return real_open(file, mode, *args, **kwargs)

    with patch.object(builtins, "open", shim):
        yield calls


def test_read_jsonl_round_trips_non_ascii_with_explicit_encoding(tmp_path: Path):
    """A conversation.jsonl line holding raw non-ASCII UTF-8 must round-trip.

    Today ``Log.write_jsonl`` emits pure ASCII (``json.dumps`` defaults to
    ``ensure_ascii=True``), but a log edited by hand, written by an older or
    newer build, or produced by any future ``ensure_ascii=False`` change puts
    real UTF-8 bytes on disk. Reading those bytes must not depend on the
    platform's preferred encoding.

    The fixture writes non-ASCII bytes with ``ensure_ascii=False`` (test-only,
    to put real bytes on disk) and reads them back under a legacy single-byte
    codec shimmed into the position CPython would otherwise fill with the locale
    encoding -- so this fails on a machine of any locale if the ``encoding=`` is
    removed from ``_gen_read_jsonl``.
    """
    non_ascii = "café — 中文 — 🎉"
    jsonl_file = tmp_path / "conversation.jsonl"
    line = json.dumps(
        {
            "role": "user",
            "content": non_ascii,
            "timestamp": "2025-01-01T00:00:00+00:00",
        },
        ensure_ascii=False,
    )
    jsonl_file.write_text(line + "\n", encoding="utf-8")
    # Real multi-byte UTF-8 on disk, not "\uXXXX" ASCII escapes.
    assert b"\xc3\xa9" in jsonl_file.read_bytes()

    with _legacy_default_encoding("latin-1"):
        log = Log.read_jsonl(jsonl_file)

    assert log.messages[0].content == non_ascii


def test_write_jsonl_uses_explicit_utf8_encoding(tmp_path: Path):
    """``write_jsonl`` must open conversation.jsonl with ``encoding="utf-8"``.

    A direct spy on ``builtins.open`` proves the encoding kwarg is passed
    regardless of the machine's locale -- the locale-independent complement to
    the round-trip test. ``write_jsonl`` makes exactly one ``open()`` call, so
    that call is the one under test.
    """
    log = Log([Message("user", "hello")])
    jsonl_file = tmp_path / "conversation.jsonl"

    with _record_open_calls() as calls:
        log.write_jsonl(jsonl_file)

    assert len(calls) == 1, f"expected one open() call, got {calls}"
    assert "w" in calls[0]["mode"]
    assert calls[0]["encoding"] == "utf-8", (
        f"write_jsonl must pass encoding='utf-8', got encoding={calls[0]['encoding']!r}"
    )


def test_write_jsonl_appends_only_new_messages(tmp_path: Path):
    """Repeated persistence does not rewrite the existing conversation."""
    jsonl_file = tmp_path / "conversation.jsonl"
    log = Log([Message("user", "first")]).write_jsonl(jsonl_file, append=True)
    first_size = jsonl_file.stat().st_size

    log = log.append(Message("assistant", "second"))
    with _record_open_calls() as calls:
        log = log.write_jsonl(jsonl_file, append=True)

    assert calls[0]["mode"] == "a"
    assert jsonl_file.stat().st_size > first_size
    assert [message.content for message in Log.read_jsonl(jsonl_file)] == [
        "first",
        "second",
    ]
    assert log.persisted_messages == 2
    assert log.persisted_path == jsonl_file.resolve()


def test_write_jsonl_repairs_partial_failed_append_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A failed append must not leave bytes that a retry duplicates."""
    jsonl_file = tmp_path / "conversation.jsonl"
    log = Log([Message("user", "first")]).write_jsonl(jsonl_file, append=True)
    log = log.append(Message("assistant", "second"))
    real_open = builtins.open

    class FailingAppendWriter:
        def __init__(self, file: TextIOWrapper):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            self.file.__exit__(exc_type, exc_value, traceback)

        def writelines(self, lines) -> None:
            line = next(iter(lines))
            self.file.write(line[: len(line) // 2])
            self.file.flush()
            raise OSError("no space left on device")

    failed = False

    def fail_first_append(path, mode="r", *args, **kwargs):
        nonlocal failed
        file = real_open(path, mode, *args, **kwargs)
        if mode == "a" and not failed:
            failed = True
            return FailingAppendWriter(file)
        return file

    monkeypatch.setattr(builtins, "open", fail_first_append)
    with pytest.raises(OSError, match="no space left on device"):
        log.write_jsonl(jsonl_file, append=True)

    repaired = log.write_jsonl(jsonl_file, append=True)

    assert [message.content for message in Log.read_jsonl(jsonl_file)] == [
        "first",
        "second",
    ]
    assert repaired.persisted_messages == 2


def test_write_jsonl_rewrites_when_destination_is_truncated(tmp_path: Path):
    """A changed destination invalidates the append cursor."""
    jsonl_file = tmp_path / "conversation.jsonl"
    log = Log([Message("user", "first")]).write_jsonl(jsonl_file, append=True)
    log = log.append(Message("assistant", "second"))
    jsonl_file.write_bytes(b"")

    with _record_open_calls() as calls:
        log.write_jsonl(jsonl_file, append=True)

    assert calls[0]["mode"] == "w"
    assert [message.content for message in Log.read_jsonl(jsonl_file)] == [
        "first",
        "second",
    ]


def test_read_jsonl_with_partial_tail_rewrites_before_append(tmp_path: Path):
    """Reloading a partial final line must not make that tail append-safe."""
    jsonl_file = tmp_path / "conversation.jsonl"
    Log([Message("user", "first")]).write_jsonl(jsonl_file)
    with jsonl_file.open("ab") as file:
        file.write(b'{"role":"assistant","content":"partial')

    log = Log.read_jsonl(jsonl_file).append(Message("assistant", "second"))
    with _record_open_calls() as calls:
        log.write_jsonl(jsonl_file, append=True)

    assert calls[0]["mode"] == "w"
    assert [message.content for message in Log.read_jsonl(jsonl_file)] == [
        "first",
        "second",
    ]


def test_write_jsonl_rewrites_for_a_different_destination(tmp_path: Path):
    """A persistence cursor belongs only to the file it was recorded for."""
    conversation_file = tmp_path / "conversation.jsonl"
    branch_file = tmp_path / "branches" / "dev.jsonl"
    branch_file.parent.mkdir()
    log = Log([Message("user", "inherited")]).write_jsonl(
        conversation_file, append=True
    )
    log = log.append(Message("assistant", "branch message"))

    with _record_open_calls() as calls:
        log = log.write_jsonl(branch_file, append=True)

    assert calls[0]["mode"] == "w"
    assert [message.content for message in Log.read_jsonl(branch_file)] == [
        "inherited",
        "branch message",
    ]
    assert log.persisted_path == branch_file.resolve()


def test_write_jsonl_rewrites_after_in_place_message_edit(tmp_path: Path):
    """Editing already-persisted history must reach disk, not be skipped.

    Append-mode tracks the persisted prefix by identity precisely because the
    message count is unchanged here: callers like ``_attach_tool_timings``
    rewrite one earlier message in place, and a count-only check would append
    zero lines and silently drop the edit.
    """
    jsonl_file = tmp_path / "conversation.jsonl"
    log = Log([Message("user", "first"), Message("assistant", "second")]).write_jsonl(
        jsonl_file, append=True
    )

    log.messages[-1] = log.messages[-1].replace(content="second (edited)")
    with _record_open_calls() as calls:
        log = log.write_jsonl(jsonl_file, append=True)

    assert calls[0]["mode"] == "w"
    assert [message.content for message in Log.read_jsonl(jsonl_file)] == [
        "first",
        "second (edited)",
    ]
    assert log.persisted_messages == 2


def test_write_jsonl_rewrites_after_messages_are_removed(tmp_path: Path):
    """Undo-like changes replace stale persisted trailing messages."""
    jsonl_file = tmp_path / "conversation.jsonl"
    log = Log(
        [Message("user", "first"), Message("assistant", "remove me")]
    ).write_jsonl(jsonl_file)

    log = log.pop()
    with _record_open_calls() as calls:
        log = log.write_jsonl(jsonl_file)

    assert calls[0]["mode"] == "w"
    assert [message.content for message in Log.read_jsonl(jsonl_file)] == ["first"]
    assert log.persisted_messages == 1


def test_write_jsonl_replaces_unknown_existing_file(tmp_path: Path):
    """A fresh Log object must not append onto stale on-disk content."""
    jsonl_file = tmp_path / "conversation.jsonl"
    jsonl_file.write_text('{"role":"user","content":"stale"}\n')

    with _record_open_calls() as calls:
        Log([Message("user", "fresh")]).write_jsonl(jsonl_file)

    assert calls[0]["mode"] == "w"
    assert [message.content for message in Log.read_jsonl(jsonl_file)] == ["fresh"]


def test_read_jsonl_uses_explicit_utf8_encoding(tmp_path: Path):
    """``read_jsonl`` must open conversation.jsonl with ``encoding="utf-8"``.

    Symmetric to the write-side spy. ``_gen_read_jsonl`` makes exactly one
    ``open()`` call (the ``Path.stat`` for the mtime fallback is not an open), so
    that call is the one under test.
    """
    jsonl_file = tmp_path / "conversation.jsonl"
    jsonl_file.write_text(
        json.dumps(
            {
                "role": "user",
                "content": "hello",
                "timestamp": "2025-01-01T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with _record_open_calls() as calls:
        Log.read_jsonl(jsonl_file)

    assert len(calls) == 1, f"expected one open() call, got {calls}"
    assert calls[0]["encoding"] == "utf-8", (
        f"read_jsonl must pass encoding='utf-8', got encoding={calls[0]['encoding']!r}"
    )
