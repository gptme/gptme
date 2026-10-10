"""Regression coverage for the native-compaction review findings."""

from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch

from gptme.logmanager import LogManager, prepare_messages
from gptme.message import Message
from gptme.tools.autocompact import native_anthropic as native
from gptme.tools.autocompact.resume import (
    _RESULT_STUBS_PREFIX,
    _apply_native_compaction,
    _resume_via_llm,
)


def _block(text: str) -> Message:
    return Message(
        "assistant",
        text,
        metadata={
            "anthropic_compaction_block": {
                "type": "compaction",
                "content": text,
                "signature": "signed",
            }
        },
    )


def _native_run(
    manager: LogManager,
    monkeypatch: MonkeyPatch,
    *,
    keep_head: int = 0,
    budget: int = 10000,
) -> tuple[bool, list[Message]]:
    captured: list[Message] = []
    monkeypatch.setattr(native, "anthropic_compaction_supported", lambda _: True)

    def compact(messages: list[Message], *args: object, **kwargs: object) -> Message:
        captured.extend(messages)
        return _block("new summary")

    monkeypatch.setattr(native, "anthropic_native_compact", compact)
    monkeypatch.setattr(
        "gptme.tools.autocompact.resume.get_context_budget",
        lambda *args, **kwargs: budget,
    )
    gen = _apply_native_compaction(
        manager,
        prepare_messages(manager.log.messages),
        use_view_branch=False,
        compact_instructions=None,
        keep_recent_tokens=30,
        keep_head=keep_head,
        model_meta=SimpleNamespace(model="gpt-4", context=10000, max_output=100),
        llm_unlocked=None,
    )
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return bool(stop.value), captured


def _history() -> list[Message]:
    messages = [Message("system", "system prompt")]
    for i in range(5):
        messages.append(Message("user", f"user step {i} " + "work " * 50))
        messages.append(Message("assistant", f"assistant step {i} " + "work " * 50))
    return messages


def test_native_manual_compaction_preserves_recall_after_reload(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    messages = _history()
    messages.insert(3, Message("system", "original result", call_id="old-call"))
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    applied, _ = _native_run(manager, monkeypatch)
    assert applied
    reloaded = LogManager.load(manager.logdir, lock=False)
    assert reloaded.master_log.messages[3].content == "original result"
    assert reloaded.master_log.messages[3].call_id == "old-call"


def test_native_recompaction_replaces_block_in_retained_head(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    messages = _history()
    old = _block("old summary")
    messages.insert(2, old)
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    applied, captured = _native_run(manager, monkeypatch, keep_head=3)
    assert applied
    assert old in captured
    blocks = [m for m in manager.log.messages if native.compaction_block_of(m)]
    assert len(blocks) == 1
    assert blocks[0].content == "new summary"


def test_native_over_budget_falls_back_without_changing_history(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    messages = _history()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    applied, _ = _native_run(manager, monkeypatch, budget=1)
    assert applied is False
    assert manager.log.messages == messages


def test_generic_retains_user_quoting_catalog_in_head(tmp_path: Path) -> None:
    quoted = Message("user", _RESULT_STUBS_PREFIX + "\nDo not drop this instruction.")
    messages = [Message("system", "system prompt"), quoted] + _history()[1:]
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=True,
            keep_head=2,
            keep_recent_tokens=0,
            checkpoint_response=Message("assistant", "## Objective\nContinue."),
        )
    )
    assert quoted in manager.log.messages


def test_oversized_catalog_keeps_recent_context(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    messages = [Message("system", "system prompt")]
    for i in range(50):
        messages.extend(
            [
                Message("assistant", f"call {i}"),
                Message("system", f"result {i}", call_id=f"call-{i}"),
            ]
        )
    recent = Message("user", "Latest instruction must stay verbatim.")
    messages.extend([recent, Message("assistant", "Working on latest instruction.")])
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    monkeypatch.setattr(
        "gptme.tools.autocompact.resume.get_default_model",
        lambda: SimpleNamespace(model="gpt-4", context=10000, max_output=100),
    )
    monkeypatch.setattr(
        "gptme.tools.autocompact.resume.get_context_budget",
        lambda *args, **kwargs: 200,
    )
    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=True,
            keep_recent_tokens=40,
            checkpoint_response=Message("assistant", "## Objective\nContinue."),
        )
    )
    assert recent in manager.log.messages


def test_native_budget_sizes_tail_before_summary_request(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from gptme.util.tokens import len_tokens

    messages = _history()
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    applied, captured = _native_run(manager, monkeypatch, budget=200)
    assert applied
    assert len_tokens(manager.log.messages, model="gpt-4") <= 200
    # Each original body message is either summarized or retained verbatim.
    assert all(m in captured or m in manager.log.messages for m in messages[1:])


def test_generic_retains_user_quoting_catalog_in_tail(tmp_path: Path) -> None:
    quoted = Message("user", _RESULT_STUBS_PREFIX + "\nKeep this recent instruction.")
    messages = _history() + [quoted, Message("assistant", "Following instruction.")]
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    list(
        _resume_via_llm(
            manager,
            messages,
            use_view_branch=True,
            keep_recent_tokens=100,
            checkpoint_response=Message("assistant", "## Objective\nContinue."),
        )
    )
    assert quoted in manager.log.messages


def test_repeated_manual_compaction_preserves_original_recall_ids(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    messages = _history()
    messages.insert(3, Message("system", "original result", call_id="old-call"))
    manager = LogManager(messages, logdir=tmp_path / "conversation", lock=False)
    manager.write()
    assert _native_run(manager, monkeypatch)[0]
    for message in _history()[1:]:
        manager.append(message)
    assert _native_run(manager, monkeypatch)[0]
    reloaded = LogManager.load(manager.logdir, lock=False)
    assert reloaded.master_log.messages[3].content == "original result"
    assert reloaded.master_log.messages[3].call_id == "old-call"
