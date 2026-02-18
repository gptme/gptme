from pathlib import Path

from gptme.dirs import get_logs_dir
from gptme.logmanager import LogManager, Message


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


def test_write_persists_main_branch_when_on_other_branch(tmp_path: Path):
    """Regression test: writing while on a non-main branch should also persist
    the main branch to conversation.jsonl."""
    log = LogManager(logdir=tmp_path)
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
    dev_path = tmp_path / "branches" / "dev.jsonl"
    assert dev_path.exists()
    dev_content = dev_path.read_text()
    assert "dev message" in dev_content
