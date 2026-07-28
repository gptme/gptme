"""Tests for the Textual TUI (requires the `tui` extra)."""

import pytest

pytest.importorskip("textual")

from textual.color import Color
from textual.filter import ANSIToTruecolor
from textual.widgets import Collapsible, Static

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tui.app import (
    AssistantMessage,
    BouncingError,
    ChatInput,
    CostMessage,
    GptmeApp,
    InfoMessage,
    StreamingMessage,
    SystemMessage,
    ToolPlaceholder,
    UserMessage,
    _append_pt_history,
    _has_tool_calls,
    _load_pt_history,
    _markdown_tool_renderable,
    _split_all_tool_calls,
    _split_markdown_tool_calls,
    _split_thinking,
    _split_tool_calls,
    _split_xml_tool_calls,
    _summarize,
    _tool_call_renderable,
    _xml_tool_renderables,
    renderables_for_message,
)


def make_manager(tmp_path, msgs: list[Message] | None = None) -> LogManager:
    return LogManager(msgs or [], logdir=tmp_path / "test-conversation", lock=False)


def test_summarize():
    assert _summarize("hello\nworld") == "hello (2 lines)"
    assert _summarize("```stdout\nfoo\n```").startswith("stdout")
    long = "x" * 200
    assert len(_summarize(long)) < 100


@pytest.mark.asyncio
async def test_ansi_default_survives_output_filter(tmp_path):
    """Terminal-default colors must reach the driver without RGB conversion."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)

    assert not any(
        isinstance(filter_, ANSIToTruecolor) for filter_ in app.get_line_filters()
    )


@pytest.mark.asyncio
async def test_active_surfaces_use_ansi_default_background(tmp_path):
    """Both app modes use the terminal background without changing the palette."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        expected = Color(0, 0, 0, ansi=-1)
        assert app.theme == "textual-dark"
        assert not app.native_ansi_color
        assert app.get_css_variables()["primary"] == "#0178D4"
        assert app.screen.styles.background == expected
        assert app.query_one("#live", Static).styles.background == expected

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.styles.background == expected
        assert app.screen.styles.height is None
        assert app.screen.styles.max_height is None
        for selector in ("#chat", "#bottom", "#input", "#input-hint", "#status"):
            assert app.query_one(selector).styles.background == expected


@pytest.mark.asyncio
async def test_progress_placeholder_uses_message_background(tmp_path):
    """The placeholder text must not introduce a different background color."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        app._begin_stream()
        await pilot.pause()

        placeholder = app._stream_widget
        assert placeholder is not None
        body = placeholder._body
        assert body.styles.background == Color(0, 0, 0, 0)
        first_segment = next(iter(body.render_line(0)))
        assert first_segment.style is not None
        assert first_segment.style.bgcolor is not None
        assert first_segment.style.bgcolor.is_default


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
async def test_app_renders_inline_cost_in_quiet_tui(tmp_path, monkeypatch):
    """The TUI renders enabled costs through widgets despite quiet core output."""
    monkeypatch.setenv("GPTME_SHOW_COST", "1")
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    msg = Message(
        "assistant",
        "hi there!",
        metadata={
            "cost": 0.004,
            "usage": {"input_tokens": 1000, "output_tokens": 200},
        },
    )

    async with app.run_test() as pilot:
        app._on_step_message(msg)
        await pilot.pause()
        cost = app.query_one(CostMessage)
        assert "$0.0040" in str(cost.render())
        assert "1.0k in" in str(cost.render())


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
async def test_history_prefix_search(tmp_path):
    """Up with a partial prefix only shows matching history entries."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", ChatInput)
        # Seed exactly two git-prefixed and one non-matching entry
        inp._history = []
        inp._push_history("git status")
        inp._push_history("ls -la")
        inp._push_history("git commit -m 'fix'")

        # type a prefix and press Up — only git-prefixed entries should appear
        inp._set_text("git")
        await pilot.press("up")
        assert inp.text.startswith("git"), (
            f"expected git-prefixed entry, got {inp.text!r}"
        )
        first_match = inp.text

        await pilot.press("up")
        assert inp.text.startswith("git"), (
            f"expected git-prefixed entry, got {inp.text!r}"
        )
        second_match = inp.text

        # The two matches must be different entries
        assert first_match != second_match

        # pressing Up past the last match doesn't change the text
        await pilot.press("up")
        assert inp.text == second_match

        # Down navigates back; final Down restores the original partial text
        await pilot.press("down")
        await pilot.press("down")
        assert inp.text == "git"


@pytest.mark.asyncio
async def test_history_prefix_search_no_edit_leak(tmp_path):
    """Edits made during one prefix search must not appear in a subsequent search."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        inp = app.query_one("#input", ChatInput)
        inp._history = []
        inp._push_history("docker ps")
        inp._push_history("git status")

        # First search: prefix "git"
        inp._set_text("git")
        await pilot.press("up")
        assert inp.text.startswith("git")

        # Edit the found entry in-place
        inp._set_text("git status --short")

        # Navigate back to the original input
        await pilot.press("down")
        assert inp.text == "git"

        # Second search: prefix "docker" — must NOT show the edited git entry
        inp._set_text("docker")
        await pilot.press("up")
        assert inp.text == "docker ps", (
            f"edit from previous search leaked into new search: got {inp.text!r}"
        )


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


SHELL_TOOL_MSG = "Running a command\n\n```shell\necho hello\n```"
TWO_SHELL_TOOLS_MSG = (
    "Running two commands\n\n```shell\necho first\n```\n\n```shell\necho second\n```"
)


@pytest.mark.asyncio
async def test_tool_placeholder_show_and_clear(tmp_path):
    """ToolPlaceholder appears after an assistant message with tool calls and disappears on tool output."""
    from gptme.tools import init_tools

    init_tools()
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 0

        # Assistant message containing a shell tool call → placeholder shown
        app._on_step_message(Message("assistant", SHELL_TOOL_MSG))
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 1

        # Tool output arrives while the batch is still active.
        app._on_step_message(Message("system", "```stdout\nhello\n```"))
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 1

        # Starting the next model step marks the tool batch complete.
        app._begin_stream()
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 0


@pytest.mark.asyncio
async def test_tool_placeholder_not_shown_for_tool_free_response(tmp_path):
    """ToolPlaceholder must NOT appear for assistant messages without tool calls."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Plain text reply — no tool call content
        app._on_step_message(Message("assistant", "Just a plain text reply"))
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 0


@pytest.mark.asyncio
async def test_tool_placeholder_persists_across_multiple_tools(tmp_path):
    """ToolPlaceholder must stay visible until ALL tool outputs from one assistant message arrive."""
    from gptme.tools import init_tools

    init_tools()
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Assistant message with two shell calls → placeholder appears
        app._on_step_message(Message("assistant", TWO_SHELL_TOOLS_MSG))
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 1

        # First tool output → placeholder must stay (one more tool pending)
        app._on_step_message(Message("system", "```stdout\nfirst\n```"))
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 1, "placeholder cleared too early"

        # A hook can emit extra system messages for one tool. These must not
        # consume the later tool's pending state.
        app._on_step_message(Message("system", "Hook note after first tool"))
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 1

        # The indicator remains after the last result until execution leaves
        # the tool batch and starts the next model step.
        app._on_step_message(Message("system", "```stdout\nsecond\n```"))
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 1

        app._begin_stream()
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 0


@pytest.mark.asyncio
async def test_tab_completion_overlay_appears(tmp_path):
    """Pressing Tab with multiple candidates shows the completions overlay."""
    from textual.widgets import Static

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.focus()
        inp.text = "/mod"  # has multiple candidates: /model, etc.
        await pilot.press("tab")
        await pilot.pause()

        overlay = app.query_one("#completions", Static)
        # If multiple candidates exist (e.g. /model and /models), overlay shows.
        # If only one, it's hidden and the input is completed directly.
        candidates = inp._tab_candidates
        if len(candidates) > 1:
            assert overlay.display, "overlay should be visible with multiple candidates"
        else:
            # single candidate → auto-completed, overlay stays hidden
            assert inp.text.startswith("/model")


@pytest.mark.asyncio
async def test_tab_completion_overlay_hides_on_non_tab(tmp_path):
    """Typing any non-Tab character dismisses the completion overlay."""
    from textual.widgets import Static

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.focus()

        # Manually put the input into a multi-candidate state
        inp._tab_candidates = ["/model", "/models"]
        inp._tab_index = 0
        inp.post_message(ChatInput.CompletionsChanged(["/model", "/models"], 0))
        await pilot.pause()

        overlay = app.query_one("#completions", Static)
        assert overlay.display, (
            "overlay should be visible after posting CompletionsChanged"
        )

        # Pressing a regular key should dismiss the overlay
        await pilot.press("a")
        await pilot.pause()
        assert not overlay.display, (
            "overlay should be hidden after typing a non-Tab key"
        )


@pytest.mark.asyncio
async def test_tab_completion_overlay_hides_on_enter(tmp_path):
    """Submitting the input dismisses the completion overlay."""
    from textual.widgets import Static

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.focus()

        # Force multi-candidate state
        inp._tab_candidates = ["/model", "/models"]
        inp._tab_index = 0
        inp.post_message(ChatInput.CompletionsChanged(["/model", "/models"], 0))
        await pilot.pause()

        overlay = app.query_one("#completions", Static)
        assert overlay.display

        # Clear the text so submit doesn't fire generation; then press enter
        inp.text = ""
        inp._tab_candidates = [
            "/model",
            "/models",
        ]  # re-set (text clear wiped it via key events in real usage)
        inp.post_message(ChatInput.CompletionsChanged(["/model", "/models"], 0))
        await pilot.pause()
        assert overlay.display

        await pilot.press("enter")
        await pilot.pause()
        assert not overlay.display, "overlay should be hidden after enter"


@pytest.mark.asyncio
async def test_tab_completion_overlay_keeps_selected_candidate_visible(tmp_path):
    """Long candidate lists render a window containing the selected candidate."""
    from textual.widgets import Static

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    candidates = [f"/command-{i}" for i in range(12)]
    async with app.run_test() as pilot:
        await pilot.pause()
        app.post_message(ChatInput.CompletionsChanged(candidates, 10))
        await pilot.pause()

        overlay = app.query_one("#completions", Static)
        rendered = str(overlay.render())
        assert "▶ /command-10" in rendered
        assert "/command-11" in rendered
        assert "/command-0" not in rendered


@pytest.mark.asyncio
async def test_tab_completion_overlay_shows_marker_on_short_list(tmp_path):
    """Short lists (fewer than max_visible) must show the selection marker."""
    from textual.widgets import Static

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    candidates = [f"/cmd-{i}" for i in range(3)]
    async with app.run_test() as pilot:
        await pilot.pause()
        # Select the last candidate (index 2) — previously start went negative
        app.post_message(ChatInput.CompletionsChanged(candidates, 2))
        await pilot.pause()

        overlay = app.query_one("#completions", Static)
        rendered = str(overlay.render())
        assert overlay.display, "overlay should be visible"
        assert "▶ /cmd-2" in rendered, "selection marker must appear on short list"


@pytest.mark.asyncio
async def test_tab_cycles_and_overlay_updates(tmp_path):
    """Repeated Tab presses cycle through candidates and update the overlay selection."""
    from textual.widgets import Static

    # Only run if there are commands that produce multiple candidates for "/mod"
    from gptme.tui.app import complete_input

    candidates = complete_input("/mod")
    if len(candidates) <= 1:
        pytest.skip("need multiple /mod* candidates for this test")

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.focus()
        inp.text = "/mod"

        # First Tab: fills common prefix or picks first candidate
        await pilot.press("tab")
        await pilot.pause()
        overlay = app.query_one("#completions", Static)

        if len(inp._tab_candidates) > 1:
            first_idx = inp._tab_index
            assert overlay.display

            # Second Tab: cycles to next candidate
            await pilot.press("tab")
            await pilot.pause()
            assert inp._tab_index != first_idx or inp._tab_index == 0
            assert overlay.display


# ─────────────────────────────────────────────────────────
# Thinking block display
# ─────────────────────────────────────────────────────────


def test_split_thinking_no_blocks():
    """Content with no thinking tags is returned as a single non-thinking segment."""
    result = _split_thinking("Hello, world!")
    assert result == [(False, "Hello, world!")]


def test_split_thinking_think_tag():
    """<think> blocks are extracted as thinking segments."""
    content = "<think>\nstep 1\n</think>\nAnswer: 42"
    result = _split_thinking(content)
    assert any(is_think and "step 1" in text for is_think, text in result)
    assert any(not is_think and "Answer: 42" in text for is_think, text in result)


def test_split_thinking_thinking_tag():
    """<thinking> blocks (the long-form tag) are also extracted."""
    content = "<thinking>\nplan here\n</thinking>\nresult"
    result = _split_thinking(content)
    assert any(is_think and "plan here" in text for is_think, text in result)
    assert any(not is_think and "result" in text for is_think, text in result)


def test_split_thinking_strips_think_sig():
    """Anthropic think-sig HTML comments are stripped from thinking content."""
    content = "<think>\n<!-- think-sig: abc123 -->\nreal thoughts\n</think>\nok"
    result = _split_thinking(content)
    thinking_texts = [text for is_think, text in result if is_think]
    assert thinking_texts
    assert all("think-sig" not in t for t in thinking_texts)
    assert any("real thoughts" in t for t in thinking_texts)


def test_split_thinking_only_block():
    """A message that is entirely thinking returns only the thinking segment."""
    content = "<think>\ninner\n</think>"
    result = _split_thinking(content)
    assert len(result) == 1
    assert result[0][0] is True
    assert "inner" in result[0][1]


@pytest.mark.asyncio
async def test_assistant_message_renders_thinking_as_collapsible(tmp_path):
    """AssistantMessage with <think> block renders a Collapsible for the thinking."""
    content = "<think>\nstep-by-step reasoning\n</think>\nFinal answer."
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        msg_widget = AssistantMessage(content)
        await app.mount(msg_widget)
        await pilot.pause()
        collapsibles = msg_widget.query(Collapsible)
        assert len(collapsibles) > 0, "Expected at least one Collapsible for thinking"
        thinking_collapsible = collapsibles.first()
        assert thinking_collapsible.collapsed, (
            "Thinking block should be collapsed by default"
        )


def test_split_multiline_tool_calls():
    """Indented JSON calls are split without consuming adjacent prose/calls."""
    content = (
        "Before\n"
        '@ipython(call-1): {\n  "code": "print(1)\\nprint(2)"\n}\n'
        '@shell(call-2): {"command": "pwd"}\n'
        "After"
    )

    segments = _split_tool_calls(content)

    assert [is_tool for is_tool, _ in segments] == [False, True, True, False]
    title, code, language = _tool_call_renderable(segments[1][1])
    assert title.startswith("▶ ipython: print(1)")
    assert code == "print(1)\nprint(2)"
    assert language == "python"


def test_split_tool_calls_indented_prose():
    """Tool calls followed by indented prose are still recognized."""
    content = (
        '@ipython(call-1): {\n  "code": "x = 1"\n}\n'
        "  This prose is indented.\n"
        "Unindented continuation."
    )

    segments = _split_tool_calls(content)

    assert segments[0][0] is True, "first segment should be a tool call"
    assert "x = 1" in segments[0][1]
    assert not segments[1][0], "second segment should be prose"


@pytest.mark.asyncio
async def test_assistant_message_no_collapsible_without_thinking(tmp_path):
    """AssistantMessage without thinking tags renders no Collapsible."""
    content = "Plain assistant reply."
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        msg_widget = AssistantMessage(content)
        await app.mount(msg_widget)
        await pilot.pause()
        collapsibles = msg_widget.query(Collapsible)
        assert len(collapsibles) == 0, "No Collapsible expected for plain content"


@pytest.mark.asyncio
async def test_inline_thinking_transition_preserves_streamed_content(tmp_path):
    """A later thinking transition must not replace inline response text."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        app._begin_stream()
        app._on_stream_token("partial response")
        await pilot.pause()

        live = app.query_one("#live")
        assert "partial response" in str(live.render())

        app._set_stream_thinking(True)
        await pilot.pause()
        assert "partial response" in str(live.render())
        assert "Thinking" not in str(live.render())


def test_streaming_message_set_thinking_updates_placeholder():
    """set_thinking calls update with 'Thinking…' / 'Generating…' while buffer is empty."""
    from unittest.mock import patch

    msg = StreamingMessage()
    with patch.object(msg._body, "update") as mock_update:
        msg.set_thinking(True)
        msg.set_thinking(False)
    assert mock_update.call_count == 2
    thinking_label = mock_update.call_args_list[0][0][0]
    generating_label = mock_update.call_args_list[1][0][0]
    assert "Thinking" in str(thinking_label)
    assert "Generating" in str(generating_label)


def test_streaming_message_set_thinking_ignored_after_tokens():
    """set_thinking is a no-op once real tokens have arrived."""
    from unittest.mock import patch

    msg = StreamingMessage()
    msg._buffer = "partial response"
    with patch.object(msg._body, "update") as mock_update:
        msg.set_thinking(True)
    mock_update.assert_not_called()


# ─────────────────────────────────────────────────────────
# Format detection across all three tool-call formats
# ─────────────────────────────────────────────────────────


def test_split_xml_tool_calls_basic():
    """<tool-use> blocks are split out as tool segments."""
    content = (
        "Before.\n<tool-use>\n<ipython>\nprint('hi')\n</ipython>\n</tool-use>\nAfter."
    )
    segments = _split_xml_tool_calls(content)
    is_tool_flags = [is_tool for is_tool, _ in segments]
    assert True in is_tool_flags, "expected at least one tool segment"
    tool_segs = [seg for is_tool, seg in segments if is_tool]
    assert any("<tool-use>" in s for s in tool_segs)


def test_split_xml_function_calls():
    """<function_calls> blocks (Haiku format) are also split out."""
    content = 'Run:\n<function_calls>\n<invoke name="shell">\nls\n</invoke>\n</function_calls>\nDone.'
    segments = _split_xml_tool_calls(content)
    tool_segs = [seg for is_tool, seg in segments if is_tool]
    assert tool_segs, "expected function_calls block to be detected as a tool segment"
    assert any("<function_calls>" in s for s in tool_segs)


def test_split_xml_tool_calls_no_xml():
    """Content with no XML tool-call blocks is returned as a single prose segment."""
    content = "Just some plain text."
    segments = _split_xml_tool_calls(content)
    assert segments == [(False, content)]


def test_xml_tool_renderables_single():
    """_xml_tool_renderables extracts tool name and code from a single-invoke block."""
    segment = "<tool-use>\n<ipython>\nprint('hello')\n</ipython>\n</tool-use>"
    renderables = _xml_tool_renderables(segment)
    assert len(renderables) == 1
    title, code, lang = renderables[0]
    assert "ipython" in title
    assert "print" in code
    assert lang == "python"


def test_xml_tool_renderables_multiple_invokes():
    """_xml_tool_renderables emits one tuple per <invoke> in a <function_calls> block."""
    segment = (
        "<function_calls>\n"
        '<invoke name="shell">\nls /tmp\n</invoke>\n'
        '<invoke name="ipython">\nprint(42)\n</invoke>\n'
        "</function_calls>"
    )
    renderables = _xml_tool_renderables(segment)
    assert len(renderables) == 2, f"expected 2 renderables, got {len(renderables)}"
    titles = [t for t, _, _ in renderables]
    assert any("shell" in t for t in titles)
    assert any("ipython" in t for t in titles)


def test_split_markdown_tool_calls_no_tools():
    """Content with no tool-call codeblocks returns a single prose segment."""
    content = "Just prose.\n\n```txt\nsome text block\n```\n"
    segments = _split_markdown_tool_calls(content)
    assert all(not is_tool for is_tool, _ in segments)


def test_split_markdown_tool_calls_basic():
    """A markdown ipython codeblock is detected and split as a tool segment."""
    from gptme.tools import init_tools

    init_tools()
    content = "Before.\n\n```ipython\nprint('hello')\n```\n\nAfter."
    segments = _split_markdown_tool_calls(content)
    tool_segs = [seg for is_tool, seg in segments if is_tool]
    assert tool_segs, f"expected tool segment but got: {segments}"
    assert any("print" in seg for seg in tool_segs)


def test_split_markdown_tool_calls_preserves_prose():
    """Prose before and after tool codeblocks is preserved as non-tool segments."""
    from gptme.tools import init_tools

    init_tools()
    content = "Before.\n\n```bash\necho hello\n```\n\nAfter."
    segments = _split_markdown_tool_calls(content)
    prose_segs = [seg for is_tool, seg in segments if not is_tool]
    assert any("Before" in s for s in prose_segs)
    assert any("After" in s for s in prose_segs)


def test_split_markdown_tool_calls_adjacent_fences():
    """Adjacent close+open fences (``````lang) yield two separate tool segments."""
    from gptme.tools import init_tools

    init_tools()
    # Model emits closing fence immediately followed by opening fence on same line:
    # ``````shell instead of ```\n```shell
    content = "```shell\necho hello\n``````shell\necho world\n```"
    segments = _split_markdown_tool_calls(content)
    tool_segs = [seg for is_tool, seg in segments if is_tool]
    assert len(tool_segs) == 2, (
        f"expected 2 tool segments but got {len(tool_segs)}: {segments}"
    )
    assert any("echo hello" in seg for seg in tool_segs)
    assert any("echo world" in seg for seg in tool_segs)


def test_markdown_tool_renderable_extracts_name_and_code():
    """_markdown_tool_renderable returns a collapsible-ready (title, code, lang) tuple."""
    from gptme.tools import init_tools

    init_tools()
    segment = "```ipython\nprint('world')\n```"
    title, code, lang = _markdown_tool_renderable(segment)
    assert "ipython" in title
    assert "print" in code
    assert lang == "python"


@pytest.mark.asyncio
async def test_assistant_message_renders_xml_tool_call_as_collapsible(tmp_path):
    """AssistantMessage with an XML tool-use block renders a Collapsible for it."""
    content = "Let me run that.\n<tool-use>\n<ipython>\nprint('hello')\n</ipython>\n</tool-use>"
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        msg_widget = AssistantMessage(content)
        await app.mount(msg_widget)
        await pilot.pause()
        collapsibles = msg_widget.query(Collapsible)
        assert len(collapsibles) > 0, "Expected Collapsible for XML tool call"
        titles = [c.title for c in collapsibles]
        assert any("ipython" in (t or "") for t in titles), (
            f"Expected 'ipython' in a collapsible title, got: {titles}"
        )


@pytest.mark.asyncio
async def test_assistant_message_renders_markdown_tool_call_as_collapsible(tmp_path):
    """AssistantMessage with a markdown-format ipython block renders a Collapsible."""
    from gptme.tools import init_tools

    init_tools()
    content = "Running code:\n\n```ipython\nprint('hello')\n```\n\nDone."
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        msg_widget = AssistantMessage(content)
        await app.mount(msg_widget)
        await pilot.pause()
        collapsibles = msg_widget.query(Collapsible)
        assert len(collapsibles) > 0, "Expected Collapsible for markdown tool call"
        titles = [c.title for c in collapsibles]
        assert any("ipython" in (t or "") for t in titles), (
            f"Expected 'ipython' in a collapsible title, got: {titles}"
        )


# Inline mode (renderables_for_message) format detection
# ────────────────────────────────────────────────────────


def test_renderables_for_message_inline_xml_tool_call():
    """renderables_for_message renders XML tool calls as Rich Panels, not raw text."""
    from rich.panel import Panel

    content = "Let me run it.\n<tool-use>\n<ipython>\nprint(1)\n</ipython>\n</tool-use>"
    msg = Message("assistant", content)
    renderables = renderables_for_message(msg)
    panels = [r for r in renderables if isinstance(r, Panel)]
    assert panels, "expected at least one Panel for XML tool call in inline mode"
    titles = [p.title for p in panels]
    assert any("ipython" in str(t) for t in titles), (
        f"Expected 'ipython' in a Panel title, got: {titles}"
    )


def test_renderables_for_message_inline_markdown_tool_call():
    """renderables_for_message renders markdown tool calls as Rich Panels."""
    from rich.panel import Panel

    from gptme.tools import init_tools

    init_tools()
    content = "Running:\n\n```ipython\nprint('hello')\n```\n\nDone."
    msg = Message("assistant", content)
    renderables = renderables_for_message(msg)
    panels = [r for r in renderables if isinstance(r, Panel)]
    assert panels, "expected at least one Panel for markdown tool call in inline mode"
    titles = [p.title for p in panels]
    assert any("ipython" in str(t) for t in titles), (
        f"Expected 'ipython' in a Panel title, got: {titles}"
    )


def test_renderables_for_message_inline_multiple_xml_invokes():
    """renderables_for_message emits one Panel per invoke in a multi-invoke XML block."""
    from rich.panel import Panel

    content = (
        "<function_calls>\n"
        '<invoke name="shell">\nls\n</invoke>\n'
        '<invoke name="ipython">\nprint(2)\n</invoke>\n'
        "</function_calls>"
    )
    msg = Message("assistant", content)
    renderables = renderables_for_message(msg)
    panels = [r for r in renderables if isinstance(r, Panel)]
    assert len(panels) == 2, (
        f"expected 2 Panels for 2-invoke XML block, got {len(panels)}"
    )


# --- _split_all_tool_calls / mixed-format tests ---


def test_split_all_tool_calls_pure_tool_format():
    """@tool-only segment returns (True, 'tool', seg) for the tool call."""
    content = '@shell(abc): {"command": "ls"}'
    result = _split_all_tool_calls(content)
    tool_segs = [(fmt, seg) for is_t, fmt, seg in result if is_t]
    assert len(tool_segs) == 1
    assert tool_segs[0][0] == "tool"


def test_split_all_tool_calls_pure_xml():
    """XML-only segment returns (True, 'xml', seg)."""
    content = (
        '<function_calls>\n<invoke name="shell">\nls\n</invoke>\n</function_calls>'
    )
    result = _split_all_tool_calls(content)
    tool_segs = [(fmt, seg) for is_t, fmt, seg in result if is_t]
    assert len(tool_segs) == 1
    assert tool_segs[0][0] == "xml"


def test_split_all_tool_calls_pure_markdown():
    """Markdown codeblock tool call returns (True, 'markdown', seg)."""
    from gptme.tools import init_tools

    init_tools()
    content = "```ipython\nprint(1)\n```"
    result = _split_all_tool_calls(content)
    tool_segs = [(fmt, seg) for is_t, fmt, seg in result if is_t]
    assert len(tool_segs) == 1, f"expected 1 tool seg, got: {result}"
    assert tool_segs[0][0] == "markdown"


def test_split_all_tool_calls_mixed_tool_and_xml():
    """A segment with both @tool and XML tool calls detects both."""
    content = (
        '@shell(abc): {"command": "ls"}\n'
        "Some prose.\n"
        '<function_calls>\n<invoke name="ipython">\nprint(1)\n</invoke>\n</function_calls>'
    )
    result = _split_all_tool_calls(content)
    formats = [fmt for is_t, fmt, _ in result if is_t]
    assert "tool" in formats, "expected @tool format to be detected"
    assert "xml" in formats, "expected XML format to be detected in prose segment"


def test_split_all_tool_calls_mixed_xml_and_markdown():
    """A segment with both XML and markdown tool calls detects both."""
    from gptme.tools import init_tools

    init_tools()
    content = (
        '<function_calls>\n<invoke name="shell">\nls\n</invoke>\n</function_calls>\n'
        "Some prose.\n"
        "```ipython\nprint(2)\n```"
    )
    result = _split_all_tool_calls(content)
    formats = [fmt for is_t, fmt, _ in result if is_t]
    assert "xml" in formats, "expected XML format to be detected"
    assert "markdown" in formats, "expected markdown format to be detected in prose"


def test_has_tool_calls_detects_all_formats():
    """_has_tool_calls returns True for each of the three formats."""
    from gptme.tools import init_tools

    init_tools()
    assert _has_tool_calls('@shell(x): {"command": "ls"}')
    assert _has_tool_calls(
        '<function_calls>\n<invoke name="shell">\nls\n</invoke>\n</function_calls>'
    )
    assert _has_tool_calls("```ipython\nprint(1)\n```")
    assert not _has_tool_calls("Just plain prose with no tool calls.")


@pytest.mark.asyncio
async def test_assistant_message_mixed_tool_and_xml(tmp_path):
    """AssistantMessage renders both @tool and XML tool calls as Collapsibles."""
    content = (
        '@shell(abc): {"command": "ls"}\n'
        "Some text.\n"
        '<function_calls>\n<invoke name="ipython">\nprint(1)\n</invoke>\n</function_calls>'
    )
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        widget = AssistantMessage(content)
        await app.mount(widget)
        await pilot.pause()
        collapsibles = widget.query(Collapsible)
        assert len(collapsibles) >= 2, (
            f"expected ≥2 Collapsibles for mixed @tool+XML content, got {len(collapsibles)}"
        )


def test_renderables_for_message_mixed_formats():
    """renderables_for_message emits Panels for both @tool and XML tool calls."""
    from rich.panel import Panel

    content = (
        '@shell(abc): {"command": "ls"}\n'
        "Some prose.\n"
        '<function_calls>\n<invoke name="ipython">\nprint(1)\n</invoke>\n</function_calls>'
    )
    msg = Message("assistant", content)
    renderables = renderables_for_message(msg)
    panels = [r for r in renderables if isinstance(r, Panel)]
    assert len(panels) >= 2, (
        f"expected ≥2 Panels for mixed @tool+XML content, got {len(panels)}"
    )
