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
