"""Tests for the Textual TUI (requires the `tui` extra)."""

import pytest

pytest.importorskip("textual")

from textual.widgets import Collapsible

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tui.app import (
    AssistantMessage,
    BouncingError,
    ChatInput,
    GptmeApp,
    InfoMessage,
    SystemMessage,
    UserMessage,
    _append_pt_history,
    _load_pt_history,
    _summarize,
)


def make_manager(tmp_path, msgs: list[Message] | None = None) -> LogManager:
    return LogManager(msgs or [], logdir=tmp_path / "test-conversation", lock=False)


def test_summarize():
    assert _summarize("hello\nworld") == "hello (2 lines)"
    assert _summarize("```stdout\nfoo\n```").startswith("stdout")
    long = "x" * 200
    assert len(_summarize(long)) < 100


@pytest.mark.asyncio
async def test_app_renders_history(tmp_path):
    manager = make_manager(
        tmp_path,
        [
            Message("system", "system prompt", hide=True),
            Message("user", "hello"),
            Message("assistant", "hi there!"),
            Message("system", "```stdout\ntool output\n```"),
        ],
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(UserMessage)) == 1
        assert len(app.query(AssistantMessage)) == 1
        # hidden system prompt not rendered; tool output is, collapsed
        assert len(app.query(SystemMessage)) == 1
        collapsible = app.query_one(Collapsible)
        assert collapsible.collapsed


@pytest.mark.asyncio
async def test_queue_while_generating(tmp_path):
    manager = make_manager(tmp_path, [Message("user", "hello")])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # simulate a running generation
        app.generating = True
        inp = app.query_one("#input", ChatInput)
        inp.text = "queued prompt"
        await pilot.press("enter")
        await pilot.pause()
        assert app.prompt_queue == ["queued prompt"]
        queued = app.query(UserMessage)
        assert any("queued" in w.classes for w in queued)
        # not appended to the log while generating
        assert all(m.content != "queued prompt" for m in manager.log)


@pytest.mark.asyncio
async def test_toggle_outputs(tmp_path):
    manager = make_manager(
        tmp_path,
        [Message("system", "some output"), Message("system", "more output")],
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        collapsibles = list(app.query(Collapsible))
        assert all(c.collapsed for c in collapsibles)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(not c.collapsed for c in collapsibles)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(c.collapsed for c in collapsibles)


@pytest.mark.asyncio
async def test_slash_command_help(tmp_path):
    """Slash-commands route through the CLI command registry."""
    manager = make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/help"
        await pilot.press("enter")
        await pilot.pause()
        infos = list(app.query(InfoMessage))
        assert infos, "expected /help output to be shown"


@pytest.mark.asyncio
async def test_experimental_jelly_errors_show_recovery_hints(tmp_path):
    app = GptmeApp(make_manager(tmp_path), experimental_jelly_errors=True)
    async with app.run_test() as pilot:
        app._show_info("Command failed", error=True)
        # Poll until the recovery hint appears (callback fires after ~0.3s).
        # A fixed wait races on busy runners; polling converges faster and reliably.
        for _ in range(40):
            await pilot.pause(0.025)
            error = app.query_one(BouncingError)
            if "Recovery:" in str(error.render()):
                break
        else:
            pytest.fail("Recovery hint did not appear within 1s")
        rendered = error.render()
        assert "Recovery:" in str(rendered)
        assert "retry" in str(rendered)


@pytest.mark.asyncio
async def test_jelly_errors_are_disabled_by_default(tmp_path):
    app = GptmeApp(make_manager(tmp_path))
    async with app.run_test() as pilot:
        app._show_info("Command failed", error=True)
        await pilot.pause()
        assert not app.query(BouncingError)
        assert app.query_one(InfoMessage).has_class("error")


@pytest.mark.asyncio
async def test_path_prompt_not_treated_as_command(tmp_path):
    """Absolute paths (/tmp/foo.md) are prompts (with include_paths), not commands."""
    somefile = tmp_path / "notes.md"
    somefile.write_text("hello notes")
    manager = make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.generating = True  # queue instead of hitting the LLM
        inp = app.query_one("#input", ChatInput)
        inp.text = str(somefile)
        await pilot.press("enter")
        await pilot.pause()
        # queued as a prompt, not executed (or rejected) as a command
        assert app.prompt_queue == [str(somefile)]


@pytest.mark.asyncio
async def test_interactive_command_fails_fast(tmp_path):
    """Commands that prompt on stdin get EOF and a helpful error, not a hang."""
    manager = make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/impersonate"  # prompts via input() when no args given
        await pilot.press("enter")
        await pilot.pause()
        infos = [str(i.render()) for i in app.query(InfoMessage)]
        assert any("interactive input" in i for i in infos), infos


def test_complete_input_commands():
    """Completion reuses the CLI command registry and completers."""
    from gptme.tui.app import complete_input

    candidates = complete_input("/mod")
    assert "/model" in candidates
    assert all(c.startswith("/mod") for c in candidates)
    # no completions for regular text
    assert complete_input("hello") == []


@pytest.mark.asyncio
async def test_tab_completes_command(tmp_path):
    manager = make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.focus()
        inp.text = "/mode"
        await pilot.press("tab")
        await pilot.pause()
        # completes towards /model (single candidate or common prefix)
        assert inp.text.startswith("/model")
        # tab must not switch focus away from the input
        assert app.focused is inp


@pytest.mark.asyncio
async def test_history_preserves_edits_while_browsing(tmp_path):
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", ChatInput)
        inp._push_history("previous prompt")
        inp._set_text("draft prompt")

        await pilot.press("up")
        assert inp.text == "previous prompt"
        inp._set_text("edited previous prompt")

        await pilot.press("down")
        assert inp.text == "draft prompt"
        await pilot.press("up")
        assert inp.text == "edited previous prompt"


@pytest.mark.asyncio
async def test_word_navigation(tmp_path):
    """Alt+Left/Right navigate by word boundary in the input."""
    # "hello world foo": h=0 e=1 l=2 l=3 o=4 ' '=5 w=6 ... d=10 ' '=11 f=12 o=13 o=14
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", ChatInput)
        inp.focus()
        inp.text = "hello world foo"
        inp.move_cursor(inp.document.end)  # column 15
        await pilot.pause()

        await pilot.press("alt+left")
        await pilot.pause()
        _, col = inp.cursor_location
        assert col == 12, f"expected col 12 (start of 'foo'), got {col}"

        await pilot.press("alt+left")
        await pilot.pause()
        _, col = inp.cursor_location
        assert col == 6, f"expected col 6 (start of 'world'), got {col}"

        # from col 0, word-right jumps to end of 'hello' (col 5)
        inp.move_cursor((0, 0))
        await pilot.pause()
        await pilot.press("alt+right")
        await pilot.pause()
        _, col = inp.cursor_location
        assert col == 5, f"expected col 5 (end of 'hello'), got {col}"


def test_pt_history_roundtrip(tmp_path):
    """_append_pt_history / _load_pt_history are inverse operations."""
    hist_file = tmp_path / "history.pt"
    _append_pt_history(hist_file, "first entry")
    _append_pt_history(hist_file, "second entry")
    entries = _load_pt_history(hist_file)
    assert entries == ["first entry", "second entry"]


def test_pt_history_missing_file(tmp_path):
    """Loading a non-existent file returns an empty list."""
    assert _load_pt_history(tmp_path / "no-such-file.pt") == []


def test_pt_history_write_error_is_isolated(tmp_path, monkeypatch):
    """A write failure in _append_pt_history must not propagate out of _push_history."""
    import gptme.tui.app as tui_app

    hist_file = tmp_path / "history.pt"
    # Route ChatInput init to the empty tmp file
    monkeypatch.setattr(tui_app, "get_pt_history_file", lambda: hist_file)
    # Simulate a read-only filesystem for all subsequent writes
    monkeypatch.setattr(
        tui_app,
        "_append_pt_history",
        lambda *_: (_ for _ in ()).throw(OSError("disk full")),
    )

    # _push_history must swallow the error; the in-memory history still grows
    chat_input = ChatInput()
    chat_input._push_history("hello")  # must not raise
    assert chat_input._history == ["hello"]


def test_pt_history_concurrent_tui_and_cli_appends(tmp_path):
    """TUI and CLI writers share one lock and cannot interleave entries."""
    import threading

    from gptme.util.history import LockedFileHistory

    hist_file = tmp_path / "history.pt"
    entries = [f"entry-{i}\nline-{i}" for i in range(20)]
    errors: list[Exception] = []

    def writer(index: int, text: str) -> None:
        try:
            if index % 2:
                _append_pt_history(hist_file, text)
            else:
                LockedFileHistory(hist_file).append_string(text)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=writer, args=(i, entry))
        for i, entry in enumerate(entries)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    loaded = _load_pt_history(hist_file)
    assert sorted(loaded) == sorted(entries)


@pytest.mark.asyncio
async def test_history_persists_across_sessions(tmp_path, monkeypatch):
    """TUI history is written to and read from the shared pt history file."""
    hist_file = tmp_path / "history.pt"

    # Seed the history file with one pre-existing entry (simulates a prior CLI run)
    _append_pt_history(hist_file, "prior cli entry")

    # Patch get_pt_history_file so both sessions use the same tmp file
    import gptme.tui.app as tui_app

    monkeypatch.setattr(tui_app, "get_pt_history_file", lambda: hist_file)

    # First TUI session: should load the prior entry and add a new one
    app1 = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app1.run_test():
        inp1 = app1.query_one("#input", ChatInput)
        assert inp1._history == ["prior cli entry"]
        inp1._push_history("tui entry one")
        assert inp1._history == ["prior cli entry", "tui entry one"]

    # Second TUI session: should see both entries from file
    app2 = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app2.run_test():
        inp2 = app2.query_one("#input", ChatInput)
        assert inp2._history == ["prior cli entry", "tui entry one"]
