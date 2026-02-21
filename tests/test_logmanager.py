from pathlib import Path

import pytest

from gptme.dirs import get_logs_dir
from gptme.logmanager import Log, LogManager, Message, check_for_modifications
from gptme.tools import init_tools


@pytest.fixture(autouse=True)
def _init_tools():
    """Ensure tools are loaded for check_for_modifications tests."""
    init_tools(allowlist=["save", "patch", "append"])


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
