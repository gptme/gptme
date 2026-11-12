"""TUI conversation names: auto-naming, statusline payload, terminal title (#4878)."""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from gptme.config import ChatConfig
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tui.app import GptmeApp, _CommandResult
from gptme.tui.statusline import LogStats, build_payload
from gptme.util.terminal import POP_TITLE, PUSH_TITLE, app_title, title_sequence

# the package re-exports the chat() function under the module's name
chat_module = sys.modules["gptme.chat"]


def _payload(name: str | None) -> dict:
    return build_payload(
        session="2026-11-12-flying-sleepy-mermaid",
        name=name,
        logdir=Path("/tmp/s"),
        workspace=Path("/tmp"),
        model=None,
        context_size=None,
        tokens=None,
        cost=0.0,
        duration_ms=0,
        state="idle",
        queued=0,
        stats=LogStats(),
    )


def test_statusline_session_name_is_conversation_name():
    payload = _payload("fix-tui-titles")
    assert payload["session_name"] == "fix-tui-titles"
    assert payload["session_id"] == "2026-11-12-flying-sleepy-mermaid"


def test_statusline_session_name_falls_back_to_id():
    payload = _payload(None)
    assert payload["session_name"] == "2026-11-12-flying-sleepy-mermaid"
    assert payload["session_id"] == "2026-11-12-flying-sleepy-mermaid"


def test_title_has_stable_prefix_and_strips_control_chars():
    assert app_title("fix-tui-titles") == "gptme: fix-tui-titles"
    assert app_title(None) == "gptme"
    # a model-generated name must not end the OSC early and inject sequences
    assert title_sequence("evil\x07\x1b]0;pwned\x9b2J") == "\x1b]0;evil]0;pwned2J\x07"


def _named_logdir(tmp_path: Path, name: str | None) -> Path:
    logdir = tmp_path / "2026-11-12-flying-sleepy-mermaid"
    logdir.mkdir(parents=True)
    if name:
        config = ChatConfig.from_logdir(logdir)
        config.name = name
        config.save()
    return logdir


def _spy_driver(monkeypatch) -> list[str]:
    from textual.drivers.headless_driver import HeadlessDriver

    writes: list[str] = []

    def write(self, data: str) -> None:
        if "\x1b]0;" in data or PUSH_TITLE in data or POP_TITLE in data:
            writes.append(data)

    monkeypatch.setattr(HeadlessDriver, "write", write)
    return writes


@pytest.mark.asyncio
async def test_resumed_named_conversation_shows_name_and_restores_title(
    tmp_path, monkeypatch
):
    """A named conversation shows its name at once: in the statusline payload
    and the terminal title, pushed on start and popped on exit."""
    logdir = _named_logdir(tmp_path, "fix-tui-titles")
    app = GptmeApp(LogManager([], logdir=logdir, lock=False), workspace=tmp_path)
    app._statusline_cmd = "true"
    payloads: list[dict] = []
    monkeypatch.setattr(app, "_request_statusline", payloads.append)
    writes = _spy_driver(monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert writes[0] == PUSH_TITLE + title_sequence("gptme: fix-tui-titles")
        assert payloads[-1]["session_name"] == "fix-tui-titles"
        assert payloads[-1]["session_id"] == logdir.name

        # /rename (or any command changing config.toml) updates both
        config = ChatConfig.from_logdir(logdir)
        config.name = "renamed"
        config.save()
        app._finish_command("rename", [], _CommandResult(handled=True))
        assert writes[-1] == title_sequence("gptme: renamed")
        assert payloads[-1]["session_name"] == "renamed"
    assert writes[-1] == POP_TITLE


@pytest.mark.asyncio
async def test_unnamed_conversation_titles_with_id_until_named(tmp_path, monkeypatch):
    logdir = _named_logdir(tmp_path, None)
    app = GptmeApp(LogManager([], logdir=logdir, lock=False), workspace=tmp_path)
    app._statusline_cmd = None
    writes = _spy_driver(monkeypatch)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert writes == [PUSH_TITLE + title_sequence(f"gptme: {logdir.name}")]
        app._set_conversation_name("fix-tui-titles")
        assert writes[-1] == title_sequence("gptme: fix-tui-titles")
        n = len(writes)
        app._set_conversation_name("fix-tui-titles")  # unchanged: no rewrite
        assert len(writes) == n
    # a name landing after exit (naming thread) is not written
    app._set_conversation_name("late")
    assert writes[-1] == POP_TITLE


def test_tui_turn_triggers_shared_auto_naming(tmp_path, monkeypatch):
    """The TUI step loop auto-names via the CLI's shared trigger and hands the
    name to the UI thread."""
    logdir = _named_logdir(tmp_path, None)
    manager = LogManager([Message("user", "fix the titles")], logdir=logdir, lock=False)
    app = GptmeApp(manager, workspace=tmp_path)

    named: list[tuple] = []
    done = threading.Event()

    def try_auto_name(config, messages, model):
        assert model == "test/model"
        assert [m.role for m in messages] == ["user", "assistant"]
        config.name = "fix-tui-titles"
        config.save()
        done.set()
        return config.name

    def call_from_thread(callback, *args, **kwargs):
        # not mounted: record what reaches the UI thread instead of running it
        if callback == app._set_conversation_name:
            named.append(args)

    monkeypatch.setattr(chat_module, "try_auto_name", try_auto_name)
    monkeypatch.setattr(
        chat_module,
        "get_default_model",
        lambda: SimpleNamespace(full="test/model"),
    )
    chat_module._naming_threads.clear()
    monkeypatch.setattr(app, "call_from_thread", call_from_thread)
    monkeypatch.setattr(app, "_restore_terminal", lambda: None)
    monkeypatch.setattr("gptme.tui.app.trigger_hook", lambda *a, **kw: [])
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)
    monkeypatch.setattr(
        "gptme.tui.app.step", lambda *a, **kw: iter([Message("assistant", "Done.")])
    )

    app._generation_body()

    assert done.wait(5)
    thread = chat_module._naming_threads.get(logdir)
    if thread is not None:
        thread.join(5)
    assert ChatConfig.from_logdir(logdir).name == "fix-tui-titles"
    assert named == [("fix-tui-titles",)]


def test_auto_naming_skipped_outside_window_or_when_named(tmp_path, monkeypatch):
    """No thread once named or after MAX_ASSISTANT_MSGS_FOR_NAMING replies."""
    started: list[Path] = []
    monkeypatch.setattr(
        chat_module,
        "_start_auto_naming_thread",
        lambda logdir, *a, **kw: started.append(logdir),
    )
    monkeypatch.setattr(
        chat_module, "get_default_model", lambda: SimpleNamespace(full="test/model")
    )
    user = Message("user", "hi")
    reply = Message("assistant", "hello")

    unnamed = _named_logdir(tmp_path / "a", None)
    chat_module.maybe_start_auto_naming(unnamed, [user])  # no reply yet
    late = [user] + [reply] * (chat_module.MAX_ASSISTANT_MSGS_FOR_NAMING + 1)
    chat_module.maybe_start_auto_naming(unnamed, late)
    named = _named_logdir(tmp_path / "b", "already")
    chat_module.maybe_start_auto_naming(named, [user, reply])
    assert started == []

    chat_module.maybe_start_auto_naming(unnamed, [user, reply])
    assert started == [unnamed]
