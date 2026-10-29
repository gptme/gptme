"""Tests for the Textual TUI (requires the `tui` extra)."""

import io
import json
import signal
import sys
from pathlib import Path

import pytest

pytest.importorskip("textual")

from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text
from textual.color import Color
from textual.filter import ANSIToTruecolor
from textual.widgets import Collapsible, Static

from gptme.logmanager import LogManager
from gptme.message import Message, MessageMetadata
from gptme.tui.app import (
    AssistantMessage,
    BouncingError,
    ChatInput,
    ChatMarkdown,
    ConfirmScreen,
    CostMessage,
    GptmeApp,
    InfoMessage,
    StreamingMessage,
    StreamProse,
    SystemMessage,
    ToolPlaceholder,
    UserMessage,
    _append_pt_history,
    _find_open_tool_fence_pos,
    _has_tool_calls,
    _load_pt_history,
    _markdown_tool_renderable,
    _split_all_tool_calls,
    _split_markdown_tool_calls,
    _split_streaming_markdown_tool_calls,
    _split_thinking,
    _split_tool_calls,
    _split_xml_tool_calls,
    _streaming_all_segments,
    _streaming_tool_stub,
    _strip_thinking,
    _summarize,
    _tool_call_renderable,
    _xml_tool_renderables,
    complete_input,
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
        for selector in (
            "#chat",
            "#bottom",
            "#input-row",
            "#input",
            "#status-row",
            "#status",
        ):
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
        [
            Message("system", "some output\nline 2"),
            Message("system", "more output\nline 2"),
            Message("assistant", "<think>\nreasoning\n</think>\nAnswer"),
        ],
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        collapsibles = list(app.query(Collapsible))
        assert any(c.has_class("thinking-block") for c in collapsibles)
        assert all(c.collapsed for c in collapsibles)
        # Ctrl+O expands outputs and thinking together
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(not c.collapsed for c in collapsibles)
        assert app.show_thinking
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(c.collapsed for c in collapsibles)
        # partially expanded (thinking only): Ctrl+O expands everything
        app._set_thinking(True)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(not c.collapsed for c in collapsibles)


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


def _write_skill(root, name: str, description: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    )


@pytest.mark.asyncio
async def test_skill_command_drains_prompt_queue_and_starts_generation(
    tmp_path, monkeypatch
):
    """/skill:<name> should become a user prompt and start a TUI turn."""
    from gptme.lessons.index import LessonIndex, clear_cache
    from gptme.lessons.skill_commands import (
        register_skill_commands,
        unregister_skill_commands,
    )
    from gptme.prompt_queue import get_prompt_queue_path

    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill(skills_root, "parity-demo", "A parity skill", "Do the parity thing.")
    monkeypatch.setattr(
        LessonIndex, "_default_dirs", staticmethod(lambda: [skills_root])
    )
    clear_cache()
    register_skill_commands()
    try:
        manager = make_manager(tmp_path)
        app = GptmeApp(manager, workspace=tmp_path)
        started: list[bool] = []

        def _fake_start() -> None:
            started.append(True)

        monkeypatch.setattr(app, "_start_generation", _fake_start)

        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.query_one("#input", ChatInput)
            inp.text = "/skill:parity-demo"
            await pilot.press("enter")
            await pilot.pause()

            user_contents = [m.content for m in manager.log if m.role == "user"]
            assert any(
                "Skill invoked: /skill:parity-demo" in c and "Do the parity thing." in c
                for c in user_contents
            )
            assert "/skill:parity-demo" not in user_contents
            assert not get_prompt_queue_path(manager.logdir).exists()
            assert started, (
                "TUI should start generation after draining the skill prompt"
            )
            assert any(
                "Do the parity thing." in w.content for w in app.query(UserMessage)
            )
            skill_msgs = [
                m
                for m in manager.log
                if m.role == "user" and "Skill invoked: /skill:parity-demo" in m.content
            ]
            assert skill_msgs
            assert skill_msgs[0].metadata is not None
            assert skill_msgs[0].metadata.get("skill_invocation_id")
    finally:
        unregister_skill_commands()
        clear_cache()


@pytest.mark.asyncio
async def test_batched_tui_drain_preserves_skill_invocation_metadata(
    tmp_path, monkeypatch
):
    """Later drained skill prompts must keep invocation metadata in the TUI queue."""
    from gptme.prompt_queue import queue_prompt

    manager = make_manager(tmp_path)
    app = GptmeApp(manager, workspace=tmp_path)
    monkeypatch.setattr(app, "_start_generation", lambda: None)

    queue_prompt(manager.logdir, "first prompt")
    queue_prompt(
        manager.logdir,
        "skill prompt",
        skill_invocation_id="invocation-queued-rest",
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        app._drain_command_queued_prompts()
        await pilot.pause()

        assert manager.log.messages[-1].content == "first prompt"
        assert len(app.prompt_queue) == 1
        queued = app.prompt_queue[0]
        assert isinstance(queued, Message)
        assert queued.content == "skill prompt"
        assert queued.metadata == {"skill_invocation_id": "invocation-queued-rest"}

        await app._generation_done()
        skill_msg = next(m for m in manager.log if m.content == "skill prompt")
        assert skill_msg.metadata == {"skill_invocation_id": "invocation-queued-rest"}


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


def test_complete_input_paths(tmp_path, monkeypatch):
    """Tab completes the last word as a path; directories get a slash so
    include_paths treats them as paths (a bare "journal" is prose)."""
    from gptme.tui.app import complete_input

    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "2026-10-14.md").write_text("x")
    (tmp_path / "jokes.txt").write_text("x")
    (tmp_path / ".jhidden").write_text("x")
    monkeypatch.chdir(tmp_path)
    assert complete_input("see jo") == ["see jokes.txt", "see journal/"]
    assert complete_input("see @journal/2") == ["see @journal/2026-10-14.md"]
    assert complete_input("see .j") == ["see ./.jhidden"]
    assert complete_input(f"see {tmp_path}/jou") == [f"see {tmp_path}/journal/"]
    assert complete_input(f"{tmp_path}/jou") == [f"{tmp_path}/journal/"]
    # no command matches a root path: fall back to path completion
    assert "/tmp/" in complete_input("/tm")
    assert complete_input("hello") == []
    assert complete_input("hello ") == []
    # completed names must be ones include_paths attaches
    (tmp_path / "Makefile").write_text("x")
    (tmp_path / "my notes.txt").write_text("x")
    assert complete_input("see Mak") == ["see ./Makefile"]
    assert complete_input("see @Mak") == ["see @Makefile"]
    assert complete_input("see my") == ["see `my notes.txt`"]
    assert complete_input("see @my") == ["see `@my notes.txt`"]
    # Completed directories are valid references even when submitted immediately.
    (tmp_path / "my dir").mkdir()
    (tmp_path / "my dir" / "a b.md").write_text("x")
    assert complete_input("see my d") == []  # "d" alone is the last word
    assert complete_input("see my") == ["see `my dir/`", "see `my notes.txt`"]
    assert complete_input("see `my dir/a") == ["see `my dir/a b.md`"]
    # a symlink loop is skipped, not raised
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    assert complete_input("see lo") == []


def test_complete_input_root_path_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "journal").mkdir()
    assert complete_input("/tmp see ./jou") == ["/tmp see ./journal/"]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["see ", "see @", "/tmp/notes.md\nsee "])
async def test_tab_quoted_directory_attaches_and_completes_children(
    tmp_path, monkeypatch, prefix
):
    from gptme.util.context import _find_potential_paths

    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "my dir"
    directory.mkdir()
    (directory / "child.md").write_text("child")
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        inp = app.query_one(ChatInput)
        inp.focus()
        inp._set_text(prefix + "my")
        await pilot.press("tab")
        at = "@" if prefix.endswith("@") else ""
        base = prefix.removesuffix(at) if at else prefix
        assert inp.text == base + f"`{at}my dir/`"
        assert "my dir/" in _find_potential_paths(inp.text)
        assert (
            len(inp.document.get_text_range((0, 0), inp.cursor_location))
            == len(inp.text) - 1
        )
        await pilot.press("c", "h", "tab")
        assert inp.text == base + f"`{at}my dir/child.md`"
        assert "my dir/child.md" in _find_potential_paths(inp.text)
        assert inp.cursor_location == inp.document.end


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["see ", "see @", "/tmp/notes.md\nsee "])
@pytest.mark.parametrize("trailing", ["", " then summarize\nwithout losing this"])
async def test_tab_quoted_path_preserves_trailing_text(
    tmp_path, monkeypatch, prefix, trailing
):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "my dir"
    directory.mkdir()
    for name in ("child.md", "child2.md"):
        (directory / name).write_text("child")
    at = "@" if prefix.endswith("@") else ""
    base = prefix.removesuffix(at)
    start = base + f"`{at}my dir/ch"
    suffix = "`" + trailing
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        inp = app.query_one(ChatInput)
        inp.focus()
        inp._set_text(start + suffix)
        lines = start.split("\n")
        inp.move_cursor((len(lines) - 1, len(lines[-1])))
        await pilot.press("tab")
        assert inp.text == start + "ild" + suffix
        assert len(inp.document.get_text_range((0, 0), inp.cursor_location)) == len(
            start + "ild"
        )
        for name in ("child.md", "child2.md", "child.md"):
            await pilot.press("tab")
            completed = base + f"`{at}my dir/{name}"
            assert inp.text == completed + suffix
            assert len(inp.document.get_text_range((0, 0), inp.cursor_location)) == len(
                completed if trailing else completed + "`"
            )


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
async def test_running_tool_is_shown_while_it_runs(tmp_path):
    """When a tool starts, the streamed response is rendered (not left as raw
    preview) and a running line shows the call; the final message replaces
    the provisional one once the tools finish."""
    from gptme.tools import init_tools
    from gptme.tools.base import ToolUse
    from gptme.tui.app import AssistantMessage, StreamingMessage, _RunningTool

    init_tools()
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await app.wait_rendered()
        await pilot.pause()
        app._begin_stream()
        app._on_stream_token(SHELL_TOOL_MSG)
        await app.wait_rendered()
        await pilot.pause()
        assert app.query(StreamingMessage)

        app._on_tool_start(
            _RunningTool.from_tool_use(ToolUse("shell", [], "echo hello"))
        )
        await app.wait_rendered()
        await pilot.pause()
        assert not app.query(StreamingMessage)
        assert len(app.query(AssistantMessage)) == 1
        (placeholder,) = app.query(ToolPlaceholder)
        text = str(placeholder._body.render())
        assert text.startswith("Running shell · 0s") and text.endswith("  echo hello")
        assert "moves to background in" in text or "times out in" in text
        assert app.state == "running shell"

        # step() yields the assistant message after its tools finished
        app._on_step_message(Message("assistant", SHELL_TOOL_MSG))
        await app.wait_rendered()
        await pilot.pause()
        assert len(app.query(AssistantMessage)) == 1  # replaced, not duplicated
        assert app._running_tool is None
        app._on_step_message(Message("system", "```stdout\nhello\n```"))
        await app.wait_rendered()
        await pilot.pause()
        # the status line stays until the next step's stream takes its place
        # (removing it first would shrink the chat and make it jump)
        (placeholder,) = app.query(ToolPlaceholder)
        done = str(placeholder._body.render())
        assert done.startswith("Ran shell · ") and done.endswith("  echo hello")
        app._begin_stream()
        await app.wait_rendered()
        await pilot.pause()
        assert len(app.query(ToolPlaceholder)) == 0
        assert app.query(StreamingMessage)


def test_running_tool_render_and_limits(monkeypatch):
    from gptme.tools.base import ToolUse
    from gptme.tui.app import _duration, _RunningTool

    monkeypatch.setenv("GPTME_SHELL_FOREGROUND_TIMEOUT", "120")
    running = _RunningTool.from_tool_use(
        ToolUse("shell", [], "sleep 100\necho done", kwargs=None)
    )
    running.started -= 65
    rendered = running.render()
    text = str(rendered)
    # one line, timing first: a long command gets cut, not the timing
    assert "\n" not in text and rendered.no_wrap
    assert (
        text
        == "Running shell · 1m05s · moves to background in 55s  sleep 100 (+1 lines)"
    )
    # tool-format calls carry the command in kwargs; background ones don't wait
    bg = _RunningTool.from_tool_use(
        ToolUse("shell", [], None, kwargs={"command": "make", "background": "true"})
    )
    assert bg.summary == "make" and bg.limit is None
    assert _RunningTool.from_tool_use(ToolUse("save", ["a.txt"], "x")).limit is None
    assert _duration(3725) == "1h02m"


@pytest.mark.asyncio
async def test_tool_start_hook_ignores_other_threads(tmp_path):
    """Tools run by e.g. a thread-mode subagent must not drive this TUI."""
    import threading

    from gptme.hooks.types import ToolExecutePreData
    from gptme.tools.base import ToolUse

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._generation_thread = -1  # not this or any other thread
        data = ToolExecutePreData(tool_use=ToolUse("shell", [], "ls"))
        thread = threading.Thread(target=lambda: list(app._tui_tool_start_hook(data)))
        thread.start()
        thread.join(5)
        await pilot.pause()
        assert app._running_tool is None
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
async def test_running_tool_line_follows_each_tool(tmp_path):
    """One running line for a message's tools, showing the current call."""
    from gptme.tools import init_tools
    from gptme.tools.base import ToolUse
    from gptme.tui.app import _RunningTool

    init_tools()
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await app.wait_rendered()
        await pilot.pause()
        app._begin_stream()
        app._on_stream_token(TWO_SHELL_TOOLS_MSG)
        for cmd in ("echo first", "echo second"):
            app._on_tool_start(_RunningTool.from_tool_use(ToolUse("shell", [], cmd)))
            await app.wait_rendered()
            await pilot.pause()
            (placeholder,) = app.query(ToolPlaceholder)
            assert cmd in str(placeholder._body.render())
        app._on_step_message(Message("assistant", TWO_SHELL_TOOLS_MSG))
        await app.wait_rendered()
        await pilot.pause()
        assert app._running_tool is None
        app._begin_stream()  # the next step's stream replaces the status line
        await app.wait_rendered()
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


def test_split_thinking_unclosed_block():
    """An interrupted response ending inside <think> is still thinking.

    Hidden mode uses this split for completed inline output; treating the
    unclosed tail as ordinary content would leak the reasoning.
    """
    result = _split_thinking("<think>\nsecret plan")
    assert result == [(True, "secret plan")]

    result = _split_thinking("visible\n<thinking>\npartial")
    assert any(not is_think and "visible" in text for is_think, text in result)
    assert any(is_think and "partial" in text for is_think, text in result)
    assert all("partial" not in text or is_think for is_think, text in result)

    result = _split_thinking("<think>a</think>\nkeep\n<think>\nopen")
    assert any(is_think and "a" in text for is_think, text in result)
    assert any(not is_think and "keep" in text for is_think, text in result)
    assert any(is_think and "open" in text for is_think, text in result)


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "kinds"),
    [
        (["<think>reasoning</think>", "Reply"], [True, False]),
        (["Intro", "<think>reasoning</think>", "Reply"], [False, True, False]),
        (["Intro", "<thi", "nk>reasoning</think>", "Reply"], [False, True, False]),
        (["<think>first</think>", "Reply", "<thinking>second"], [True, False, True]),
    ],
)
async def test_streaming_thinking_segments_keep_final_order(tmp_path, chunks, kinds):
    """Thinking rows stay in segment order without moving already-visible prose."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app._begin_stream()
        await app.wait_rendered()
        await pilot.pause()
        sm = app._stream_widget
        assert sm is not None
        # new chat widgets are laid out hidden until rendered; measure once shown
        while sm.styles.visibility == "hidden":
            await pilot.pause(0.01)
        intro_y = None
        for chunk in chunks:
            sm.append_token(chunk)
            await app.wait_rendered()
            await pilot.pause()
            if chunks[0] == "Intro":
                intro = next(
                    child
                    for child in sm.children
                    if "Intro" in (getattr(child, "text", None) or str(child.render()))
                )
                # measure once its text is on screen (it renders off-screen first)
                while isinstance(intro, StreamProse) and not intro.shown:
                    await pilot.pause(0.01)
                if intro_y is None:
                    intro_y = intro.region.y
                assert intro.region.y == intro_y
        rows = [
            child
            for child in sm.children
            if not child.has_class("role") and child.display
        ]
        assert [child.has_class("thinking-stub") for child in rows] == kinds
        assert all("Generating" not in str(child.render()) for child in rows)
        app._on_step_message(Message("assistant", "".join(chunks)))
        await app.wait_rendered()
        await pilot.pause()
        am = app.query_one(AssistantMessage)
        final_rows = [child for child in am.children if not child.has_class("role")]
        assert [isinstance(child, Collapsible) for child in final_rows] == kinds


@pytest.mark.asyncio
async def test_streaming_hidden_thinking_has_only_one_status_row(tmp_path):
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        app._begin_stream()
        await app.wait_rendered()
        await pilot.pause()
        sm = app._stream_widget
        assert sm is not None
        sm.append_token("<think>reasoning")
        await app.wait_rendered()
        await pilot.pause()
        rows = [
            child
            for child in sm.children
            if not child.has_class("role") and child.display
        ]
        assert len(rows) == 1
        assert "Thinking" in str(rows[0].render())


@pytest.mark.asyncio
async def test_thinking_toggle_refreshes_active_stream(tmp_path, monkeypatch):
    """Toggling thinking switches between body mode (full text) and segment mode (stubs)."""
    monkeypatch.delenv("GPTME_TUI_DISPLAY_THINKING", raising=False)
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)

    def visible_segments(sm) -> list[tuple[bool, str]]:
        """(is_thinking_stub, text) for each visible segment row."""

        def get_text(row) -> str:
            if isinstance(row, StreamProse):
                return row.text
            return str(row.render())

        return [
            (row.has_class("thinking-stub"), get_text(row))
            for row in sm._segments
            if row.display
        ]

    async with app.run_test() as pilot:
        app._begin_stream()
        await app.wait_rendered()
        await pilot.pause()
        app._on_stream_token("<think>reasoning</think>Answer")
        await app.wait_rendered()
        await pilot.pause()
        sm = app._stream_widget
        assert sm is not None
        # Default: segment mode — body hidden, segments show stubs
        assert not sm._body.display
        assert visible_segments(sm) == [
            (True, "▸ Thinking (1 line)"),
            (False, "Answer"),
        ]

        # Body mode: body shows full text, all segments hidden
        app._set_thinking(True)
        await app.wait_rendered()
        await pilot.pause()
        assert sm._body.display
        assert "reasoning" in str(sm._body.render())
        assert all(not row.display for row in sm._segments)
        app._on_stream_token(" continues")
        await app.wait_rendered()
        await pilot.pause()
        assert "Answer continues" in str(sm._body.render())

        # Back to segment mode
        app._set_thinking(False)
        await app.wait_rendered()
        await pilot.pause()
        assert not sm._body.display
        assert visible_segments(sm) == [
            (True, "▸ Thinking (1 line)"),
            (False, "Answer continues"),
        ]
        app._on_stream_token(" hidden-mode tail")
        await app.wait_rendered()
        await pilot.pause()
        assert visible_segments(sm)[1] == (False, "Answer continues hidden-mode tail")

        # Back to body mode: body shows all accumulated tokens
        app._set_thinking(True)
        await app.wait_rendered()
        await pilot.pause()
        assert sm._body.display
        assert "hidden-mode tail" in str(sm._body.render())
        assert all(not row.display for row in sm._segments)


@pytest.mark.asyncio
async def test_streaming_thinking_stub_visible_when_thinking_hidden(tmp_path):
    """When show_thinking=False and thinking tokens arrive, the stub appears before prose."""

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app._begin_stream()
        await app.wait_rendered()
        await pilot.pause()

        sm = app._stream_widget
        assert sm is not None
        assert not sm.show_thinking  # default is show_thinking=False in tests

        # Stream thinking block followed by prose
        sm.append_token("<think>reasoning line 1\nreasoning line 2\n</think>")
        sm.append_token("Some visible prose")
        await app.wait_rendered()
        await pilot.pause()

        # Thinking stub should be visible in position, above the body
        stub = sm.query_one(".thinking-stub", Static)
        assert stub.display
        assert list(sm.children).index(stub) < list(sm.children).index(sm._segments[1])
        stub_text = str(stub.render())
        assert "Thinking" in stub_text

        # The body should show the visible prose
        assert any(
            "Some visible prose"
            in (getattr(child, "text", None) or str(child.render()))
            for child in sm.children
        )


@pytest.mark.asyncio
async def test_finalize_stream_in_place_no_dom_gap(tmp_path):
    """After streaming, finalizing mounts AssistantMessage at the stream widget's
    DOM position — no gap where the stream widget disappears before replacement."""
    from textual.containers import VerticalScroll

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()

        # Simulate a prior user message so there's a neighbor widget
        app._on_step_message(Message("user", "hello"))
        await app.wait_rendered()
        await pilot.pause()

        app._begin_stream()
        await app.wait_rendered()
        await pilot.pause()

        sm = app._stream_widget
        assert sm is not None

        chat = app.query_one("#chat", VerticalScroll)
        stream_idx = list(chat.children).index(sm)
        trailing = Static("Trailing sibling")
        await chat.mount(trailing)
        await app.wait_rendered()
        await pilot.pause()

        # The replacement must already be attached when removal is requested.
        from unittest.mock import patch

        original_remove = sm.remove

        def check_replacement_before_remove():
            children = list(chat.children)
            assert isinstance(children[stream_idx], AssistantMessage)
            assert children[stream_idx + 1] is sm
            assert children[stream_idx + 2] is trailing
            return original_remove()

        # removal happens once the replacement has rendered (_reveal)
        with patch.object(
            sm, "remove", side_effect=check_replacement_before_remove
        ) as remove:
            app._on_step_message(Message("assistant", "hello back"))
            await app.wait_rendered()
            await pilot.pause()
        remove.assert_called_once()

        # StreamingMessage is gone; AssistantMessage is at the same position
        assert len(app.query(StreamingMessage)) == 0
        assert len(app.query(AssistantMessage)) == 1
        am = app.query_one(AssistantMessage)
        assert list(chat.children).index(am) == stream_idx
        assert list(chat.children)[stream_idx + 1] is trailing


@pytest.mark.asyncio
async def test_zombie_flush_after_finalize_is_noop(tmp_path):
    """_flush_stream() called after _on_step_message() must be a no-op.

    Regression: a delayed flush timer could fire after the stream was already
    finalized by _on_step_message → _finalize_stream_in_place, resurrecting
    the stream widget or double-applying pending tokens.  This test verifies
    that the zombie flush clears the pending buffer and leaves exactly one
    AssistantMessage with the correct content.
    """
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        app._begin_stream()
        await app.wait_rendered()
        await pilot.pause()
        app._on_stream_token("Answer")
        # Simulate tokens that arrived after the final message was queued but
        # before the flush timer fired.
        with app._stream_lock:
            app._stream_pending.append(" trailing tokens")
            app._stream_flush_scheduled = True
        # Finalize via the normal step-message path (_finalize_stream_in_place).
        app._on_step_message(Message("assistant", "Answer trailing tokens"))
        # Zombie flush — must be a no-op after finalization.
        app._flush_stream()
        await app.wait_rendered()
        await pilot.pause()
        assert app._stream_widget is None
        assert not app.query(StreamingMessage)
        assert len(app.query(AssistantMessage)) == 1
        am = app.query_one(AssistantMessage)
        assert "Answer trailing tokens" in am.content
        assert not app._stream_pending
        assert not app._stream_flush_scheduled


# A 60-line save tool call — the exact fixture from the issue to test height stability.
_SAVE_60_LINES = (
    "Saving the file.\n\n```save hello.py\n"
    + "\n".join(f"line{i}" for i in range(1, 61))
    + "\n```"
)


@pytest.mark.asyncio
async def test_finalize_stream_height_does_not_collapse(tmp_path):
    """Content height must not drop by more than 2 rows when a streaming preview
    is finalized (regression guard for the 60-line save-call layout jump)."""

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app._begin_stream()
        await pilot.pause()

        sm = app._stream_widget
        assert sm is not None

        # Stream the entire 60-line save call token by token (chunked for speed)
        for line in _SAVE_60_LINES.splitlines(keepends=True):
            sm.append_token(line)
        await app.wait_rendered()
        await pilot.pause()

        height_before = sm.content_size.height

        # Finalize
        app._on_step_message(Message("assistant", _SAVE_60_LINES))
        await app.wait_rendered()
        await pilot.pause()

        am = app.query_one(AssistantMessage)
        height_after = am.content_size.height

        # The finalized height should not have dropped massively.
        # A 60-line tool call collapses to ~1 title row, but the surrounding prose
        # and role label stay. We allow up to 3 rows of shrinkage for normal
        # markdown-vs-raw-text reflow; anything larger is the old layout-jump bug.
        assert height_after >= height_before - 3, (
            f"height collapsed from {height_before} to {height_after} rows "
            f"(dropped {height_before - height_after})"
        )


@pytest.mark.asyncio
async def test_finalize_prose_stream_height_stable(tmp_path):
    """Prose streaming with Markdown widget: height at finalize must not shrink.

    The streaming preview uses a Markdown widget for prose (not Static), so
    heights match the final AssistantMessage render.  Regression guard for the
    prose reflow described in #4331 (remaining after #4342 fixed tool calls).
    """
    prose = (
        "This is a longer prose response that spans multiple lines.\n\n"
        "It has a second paragraph too, to exercise markdown reflow.\n\n"
        "And a third paragraph to make the height measurement meaningful."
    )

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app._begin_stream()
        await pilot.pause()

        sm = app._stream_widget
        assert sm is not None

        for line in prose.splitlines(keepends=True):
            sm.append_token(line)
        await pilot.pause()

        # Give the Markdown widget time to complete its async render
        await pilot.pause()

        height_before = sm.content_size.height

        # Finalize the stream
        app._on_step_message(Message("assistant", prose))
        await pilot.pause()
        await pilot.pause()

        am = app.query_one(AssistantMessage)
        height_after = am.content_size.height

        # With Markdown used during streaming, height must not collapse at finalize.
        # One-directional: the original bug (#4331) was collapse, not growth.
        # Growth can occur when the async Markdown render wasn't complete before
        # finalize — that is not a regression, so we only guard against shrinkage.
        assert height_after >= height_before - 1, (
            f"prose height collapsed from {height_before} to {height_after} rows "
            f"(dropped {height_before - height_after})"
        )


# ─────────────────────────────────────────────────────────
# Streaming tool-call collapse helpers
# ─────────────────────────────────────────────────────────


def test_streaming_tool_stub_with_path():
    """_streaming_tool_stub extracts tool name and path from the opening fence."""
    fence_text = "```save hello.py\nline1\nline2"
    stub = _streaming_tool_stub(fence_text)
    assert "save" in stub
    assert "hello.py" in stub
    assert stub.endswith("…")


def test_streaming_tool_stub_no_path():
    """_streaming_tool_stub falls back to tool name only when no path."""
    stub = _streaming_tool_stub("```ipython\nprint('hi')")
    assert "ipython" in stub
    assert stub.endswith("…")


def test_find_open_tool_fence_pos_detects_unclosed():
    """_find_open_tool_fence_pos returns offset of last unclosed tool fence."""
    from gptme.tools import init_tools

    init_tools()
    text = "Prose.\n\n```save hello.py\nline1\nline2"
    pos = _find_open_tool_fence_pos(text)
    assert pos is not None
    assert text[pos:].startswith("```save")


def test_find_open_tool_fence_pos_ignores_closed():
    """_find_open_tool_fence_pos returns None for a properly closed fence."""
    from gptme.tools import init_tools

    init_tools()
    text = "```save hello.py\nline1\n```\nMore prose."
    pos = _find_open_tool_fence_pos(text)
    assert pos is None


def test_find_open_tool_fence_pos_returns_outermost_for_nested():
    """Nested fences: the outer tool fence position is returned, not the inner."""
    from gptme.tools import init_tools

    init_tools()
    # save block with a shell example inside that is still open
    text = "```save README.md\nline1\n\n```shell\nexample code"
    pos = _find_open_tool_fence_pos(text)
    assert pos is not None
    assert text[pos:].startswith("```save"), (
        f"Expected outermost (save) fence at pos {pos!r}, got: {text[pos : pos + 20]!r}"
    )


def test_split_streaming_markdown_tool_calls_open_fence():
    """An open tool fence is returned as a tool segment even without a closing fence."""
    from gptme.tools import init_tools

    init_tools()
    content = "Saving the file.\n\n```save hello.py\nline1\nline2"
    segments = _split_streaming_markdown_tool_calls(content)
    tool_segs = [seg for is_tool, seg in segments if is_tool]
    assert tool_segs, f"expected open fence to produce a tool segment, got: {segments}"
    assert any("save" in seg for seg in tool_segs)


def test_streaming_all_segments_open_tool_call():
    """_streaming_all_segments returns a tool_call kind for an in-progress fence."""
    from gptme.tools import init_tools

    init_tools()
    buf = "Saving the file.\n\n```save hello.py\nline1\nline2"
    segs = _streaming_all_segments(buf)
    kinds = [k for k, _ in segs]
    assert "tool_call" in kinds, f"expected tool_call kind, got: {segs}"


@pytest.mark.asyncio
async def test_streaming_tool_call_shows_stub_not_raw_lines(tmp_path):
    """While streaming, a tool-call block is shown as a one-line stub, not raw lines.

    This is the core regression guard for gptme/gptme#4331: a 60-line save
    call must not stream as 60 raw lines — it should show as a single collapsed
    stub so finalizing the message causes no visible layout jump.
    """
    from gptme.tools import init_tools

    init_tools()
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        app._begin_stream()
        await pilot.pause()
        sm = app._stream_widget
        assert sm is not None

        lines = _SAVE_60_LINES.splitlines(keepends=True)
        # Stream all content lines *before* the closing fence so we test the
        # open (still-streaming) state — the key regression.  A naive impl
        # that only collapses at fence-close would show 60 raw lines here.
        for line in lines[:-1]:
            sm.append_token(line)
        await pilot.pause()

        def _visible_rows(widget):
            return [
                str(child.render())
                for child in widget.children
                if not child.has_class("role") and child.display
            ]

        rendered_open = _visible_rows(sm)
        has_raw_open = any(
            any(f"line{i}" in r for r in rendered_open) for i in range(1, 61)
        )
        assert not has_raw_open, (
            f"Raw tool-call content leaked during open streaming: {rendered_open}"
        )
        stub_rows_open = [
            r for r in rendered_open if "save" in r.lower() or "hello.py" in r
        ]
        assert stub_rows_open, (
            f"Expected a stub row while tool fence is open, got: {rendered_open}"
        )

        # Complete the call with the closing fence and re-check.
        sm.append_token(lines[-1])
        await pilot.pause()

        rendered_closed = _visible_rows(sm)
        has_raw_closed = any(
            any(f"line{i}" in r for r in rendered_closed) for i in range(1, 61)
        )
        assert not has_raw_closed, (
            f"Raw tool-call content leaked after fence closed: {rendered_closed}"
        )
        stub_rows_closed = [
            r for r in rendered_closed if "save" in r.lower() or "hello.py" in r
        ]
        assert stub_rows_closed, (
            f"Expected a stub row after fence closed, got: {rendered_closed}"
        )


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


def test_strip_thinking_closed_and_open_blocks():
    assert _strip_thinking("<think>\nhmm\n</think>\nAnswer") == "\nAnswer"
    # still streaming: cut the unclosed block
    assert _strip_thinking("Hi\n<thinking>\npartial") == "Hi\n"
    assert _strip_thinking("plain") == "plain"


def test_split_thinking_redacted_block():
    """<think redacted>...</think redacted> from Anthropic extended thinking must not leak."""
    # Closed redacted block — no thinking segment, no tag leakage
    result = _split_thinking("<think redacted>\n</think redacted>\n\nAnswer")
    for _, seg in result:
        assert "think redacted" not in seg, f"Tag leaked into segment: {seg!r}"
    prose = "".join(seg for is_think, seg in result if not is_think)
    assert "Answer" in prose

    # Unclosed redacted block still treated as thinking
    result = _split_thinking("Visible\n<think redacted>\ndata")
    prose_segs = [seg for is_think, seg in result if not is_think]
    assert any("Visible" in s for s in prose_segs)
    # "data" should not appear in non-thinking segments
    assert all("data" not in s for s in prose_segs)


def test_strip_thinking_redacted_block():
    """_strip_thinking removes <think redacted> blocks and their content."""
    # Closed redacted block
    result = _strip_thinking("<think redacted>\n</think redacted>\n\nAnswer here")
    assert "think redacted" not in result
    assert "Answer here" in result

    # Unclosed redacted block at end of stream
    result = _strip_thinking("Visible\n<think redacted>\nencrypted_data")
    assert "think redacted" not in result
    assert "encrypted_data" not in result
    assert "Visible" in result


def test_renderables_for_message_hides_thinking():
    msg = Message("assistant", "<think>\na\nb\n</think>\nFinal answer.")
    hidden = renderables_for_message(msg, show_thinking=False)
    assert not any(isinstance(r, Panel) and r.title == "Thinking" for r in hidden), (
        "thinking panel should be replaced by a stub"
    )
    assert any("2 lines hidden" in str(r) for r in hidden if isinstance(r, Text))
    shown = renderables_for_message(msg, show_thinking=True)
    assert any(isinstance(r, Panel) and r.title == "Thinking" for r in shown)


def test_renderables_for_message_hides_unclosed_thinking():
    """Interrupted <think> must not leak as ordinary markdown when hidden."""
    msg = Message("assistant", "<think>\nsecret plan")
    hidden = renderables_for_message(msg, show_thinking=False)
    blobs = " ".join(str(r) for r in hidden)
    assert "secret plan" not in blobs
    assert any("hidden" in str(r) for r in hidden if isinstance(r, Text))
    shown = renderables_for_message(msg, show_thinking=True)
    assert any(isinstance(r, Panel) and r.title == "Thinking" for r in shown)


def test_thinking_hidden_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GPTME_TUI_DISPLAY_THINKING", raising=False)
    assert not GptmeApp(make_manager(tmp_path), workspace=tmp_path).show_thinking
    monkeypatch.setenv("GPTME_TUI_DISPLAY_THINKING", "1")
    assert GptmeApp(make_manager(tmp_path), workspace=tmp_path).show_thinking


def test_complete_input_tui_commands():
    assert "/display" in complete_input("/disp")
    assert complete_input("/display ") == [
        "/display thinking",
        "/display outputs",
        "/display hidden",
        "/display highlight",
    ]
    assert complete_input("/display thinking o") == [
        "/display thinking on",
        "/display thinking off",
    ]
    assert complete_input("/display thinking on ") == []


@pytest.mark.asyncio
async def test_display_thinking_toggles_collapsibles(tmp_path, monkeypatch):
    monkeypatch.delenv("GPTME_TUI_DISPLAY_THINKING", raising=False)
    manager = make_manager(
        tmp_path, [Message("assistant", "<think>\nreasoning\n</think>\nAnswer")]
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        block = app.query(".thinking-block").results(Collapsible).__next__()
        assert block.collapsed
        inp = app.query_one("#input", ChatInput)
        for text, expanded in [
            ("/display thinking", True),
            ("/display thinking off", False),
        ]:
            inp.text = text
            await pilot.press("enter")
            await pilot.pause()
            assert app.show_thinking is expanded
            assert block.collapsed is not expanded
        # display-only toggle stays available while the agent works
        app.generating = True
        inp.text = "/display thinking on"
        await pilot.press("enter")
        await pilot.pause()
        assert app.show_thinking
        # outputs toggle leaves thinking alone
        inp.text = "/display outputs off"
        await pilot.press("enter")
        await pilot.pause()
        assert not app._outputs_expanded
        assert not block.collapsed


@pytest.mark.asyncio
async def test_inline_stream_preview_hides_thinking(tmp_path, monkeypatch):
    monkeypatch.delenv("GPTME_TUI_DISPLAY_THINKING", raising=False)
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        app._begin_stream()
        app._on_stream_token("<think>\nsecret reasoning")
        await pilot.pause()
        live = app.query_one("#live")
        assert "secret" not in str(live.render())
        assert "Thinking" in str(live.render())
        app._on_stream_token("\n</think>\nvisible answer")
        await pilot.pause()
        assert "visible answer" in str(live.render())
        assert "secret" not in str(live.render())


@pytest.mark.asyncio
async def test_inline_resize_without_textual_caret_attr(tmp_path):
    """First SIGWINCH must not crash if Textual has not parked a caret yet."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        driver = app._driver
        assert driver is not None
        writes: list[str] = []
        original_write = driver.write

        def capture(data: str) -> None:
            writes.append(data)
            original_write(data)

        driver.write = capture  # type: ignore[method-assign]
        if hasattr(app, "_previous_cursor_position"):
            delattr(app, "_previous_cursor_position")
        app._on_inline_resize()
        assert any("\x1b[J" in chunk for chunk in writes)


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="no SIGWINCH")
@pytest.mark.asyncio
async def test_inline_resize_handler_reset_on_unmount(tmp_path):
    """Unmount must drop the custom SIGWINCH handler (headless driver won't)."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._inline_sigwinch_installed
        assert signal.getsignal(signal.SIGWINCH) is not signal.SIG_DFL
    assert not app._inline_sigwinch_installed
    assert signal.getsignal(signal.SIGWINCH) is signal.SIG_DFL


@pytest.mark.asyncio
async def test_inline_queued_prompts_visible(tmp_path):
    manager = make_manager(tmp_path, [Message("user", "hello")])
    app = GptmeApp(manager, workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.generating = True
        inp = app.query_one("#input", ChatInput)
        inp.text = "queued prompt"
        await pilot.press("enter")
        await pilot.pause()
        queued = app.query_one("#queued", Static)
        assert "queued prompt" in str(queued.render())
        # interrupting hands the text back and clears the queued view
        app._interrupt_event.set()
        await app._generation_done()
        await pilot.pause()
        assert "queued prompt" not in str(queued.render())
        assert inp.text == "queued prompt"


@pytest.mark.asyncio
async def test_confirm_help_precedes_preview(tmp_path):
    """Key hints must not be clipped away by a tall preview."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(ConfirmScreen(None, "echo hi\n" * 100))
        await pilot.pause()
        dialog = app.screen.query_one("#confirm-dialog")
        ids = [child.id for child in dialog.children]
        assert ids.index("confirm-help") < ids.index("confirm-preview")


@pytest.mark.asyncio
async def test_restart_command_exits_for_reexec(tmp_path):
    """/restart can't prompt or exec under Textual: exit and let main() re-exec."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/restart"
        await pilot.press("enter")
        await pilot.pause()
    assert app.restart_requested


def test_restart_runs_session_end_before_reexec(tmp_path, monkeypatch):
    """/restart must fire SESSION_END before os.execv (shell + subagent cleanup)."""
    from gptme.tui.main import _finish_session

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    app.restart_requested = True
    order: list[object] = []
    monkeypatch.setattr(app, "end_session", lambda: order.append("end"))
    monkeypatch.setattr(
        "gptme.tools.restart._do_restart",
        lambda name, **kw: order.append(("restart", name)),
    )
    _finish_session(app, "conv")
    assert order == ["end", ("restart", "conv")]


def test_normal_exit_runs_session_end_without_reexec(tmp_path, monkeypatch):
    from gptme.tui.main import _finish_session

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    called: list[object] = []
    monkeypatch.setattr(app, "end_session", lambda: called.append("end"))
    monkeypatch.setattr(
        "gptme.tools.restart._do_restart",
        lambda name, **kw: called.append("restart"),
    )
    _finish_session(app, "conv")
    assert called == ["end"]


@pytest.mark.asyncio
async def test_restart_cli_requests_switch(tmp_path):
    """/restart cli exits the TUI with the CLI as re-exec target."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/restart cli"
        await pilot.press("enter")
        await pilot.pause()
    assert app.restart_requested
    assert app.restart_target == "cli"
    assert app.restart_web_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arg", ["bogus", "web"])
async def test_restart_bad_target_keeps_session(tmp_path, monkeypatch, arg):
    """An unknown target, or /restart web without a server, doesn't exit."""
    monkeypatch.setattr("gptme.tools.restart._http_status", lambda *a, **kw: None)
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = f"/restart {arg}"
        await pilot.press("enter")
        await pilot.pause()
        assert app.is_running
    assert not app.restart_requested


@pytest.mark.asyncio
async def test_restart_web_requests_browser(tmp_path, monkeypatch):
    """The web check runs in a worker thread, then the app exits to the UI."""
    monkeypatch.setattr(
        "gptme.tui.app.prepare_web_switch",
        lambda name: f"http://127.0.0.1:5700/chat/{name}",
    )
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/restart web"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert app.restart_requested
    name = app.manager.logdir.name
    assert app.restart_web_url == f"http://127.0.0.1:5700/chat/{name}"


@pytest.mark.asyncio
async def test_input_during_web_check_cancels_switch(tmp_path, monkeypatch):
    """A prompt submitted while /restart web is checked must not be lost."""
    import threading

    release = threading.Event()

    def slow_prepare(name):
        release.wait(5)
        return f"http://127.0.0.1:5700/chat/{name}"

    monkeypatch.setattr("gptme.tui.app.prepare_web_switch", slow_prepare)
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/restart web"
        await pilot.press("enter")
        await pilot.pause()
        assert app._web_restart_pending is not None
        # stand-in for a prompt submission (e.g. one awaiting URL confirmation)
        monkeypatch.setattr(app, "_submit", lambda text: _noop())
        inp.text = "look at https://example.com"
        await pilot.press("enter")
        await pilot.pause()
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running
    assert not app.restart_requested


async def _noop() -> None:
    return None


def test_stale_web_check_is_ignored(tmp_path, monkeypatch):
    """Only the most recent /restart web check may act."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    requested: list[object] = []
    monkeypatch.setattr(app, "_request_restart", lambda *a: requested.append(a))
    monkeypatch.setattr(app, "_show_info", lambda *a: requested.append(a))
    app._web_restart_pending = 2  # a newer check (#2) superseded #1
    app._finish_web_check(1, None, "http://old/chat/c")
    app._finish_web_check(1, "old error")
    assert requested == []
    app._finish_web_check(2, None, "http://new/chat/c")
    assert requested == [(None, "http://new/chat/c")]
    assert app._web_restart_pending is None


def test_restart_request_refused_while_generating(tmp_path, monkeypatch):
    """A prompt sent while the web check ran wins: don't exit mid-generation."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    infos: list[str] = []
    monkeypatch.setattr(app, "_show_info", lambda text, *a: infos.append(text))
    monkeypatch.setattr(app, "exit", lambda *a, **kw: infos.append("exit"))
    app.generating = True
    app._request_restart(None, "http://127.0.0.1:5700/chat/c")
    assert not app.restart_requested
    assert "exit" not in infos


def test_finish_session_switches_to_cli(tmp_path, monkeypatch):
    from gptme.tui.main import _finish_session

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    app.restart_requested = True
    app.restart_target = "cli"
    calls: list[object] = []
    monkeypatch.setattr(app, "end_session", lambda: calls.append("end"))
    monkeypatch.setattr(
        "gptme.tools.restart._do_restart",
        lambda name, **kw: calls.append((name, kw)),
    )
    _finish_session(app, "conv")
    assert calls == ["end", ("conv", {"target": "cli", "source": "tui"})]


def test_finish_session_opens_web_without_reexec(tmp_path, monkeypatch):
    from gptme.tui.main import _finish_session

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    app.restart_requested = True
    app.restart_web_url = "http://127.0.0.1:5700/chat/conv"
    calls: list[object] = []
    monkeypatch.setattr(app, "end_session", lambda: calls.append("end"))
    monkeypatch.setattr(
        "gptme.tools.restart._do_restart",
        lambda name, **kw: calls.append("restart"),
    )
    monkeypatch.setattr(
        "gptme.tools.restart.open_web", lambda url: calls.append(("web", url))
    )
    _finish_session(app, "conv")
    assert calls == ["end", ("web", "http://127.0.0.1:5700/chat/conv")]


def test_end_session_prints_cost_summary(tmp_path, capsys):
    """Exiting the TUI fires SESSION_END in the chat context (cost summary)."""
    from gptme.hooks import HookType, register_hook, unregister_hook
    from gptme.hooks.cost_awareness import session_end_cost_summary
    from gptme.util.cost_tracker import CostEntry, CostTracker

    register_hook("test.cost_summary", HookType.SESSION_END, session_end_cost_summary)
    try:
        app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
        entry = CostEntry(
            timestamp=0.0,
            model="test",
            input_tokens=2000,
            output_tokens=100,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.25,
        )
        app._chat_ctx.run(CostTracker.record, entry)
        app.end_session()
    finally:
        unregister_hook("test.cost_summary", HookType.SESSION_END)
    assert "Session: $0.25" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ctrl_d_quits_only_on_empty_input(tmp_path):
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "draft"
        inp.move_cursor((0, 0))
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert app.is_running
        assert inp.text == "raft"  # deleted forward instead
        inp.text = ""
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert not app.is_running


@pytest.mark.asyncio
async def test_status_shows_session_cost(tmp_path):
    from gptme.util.cost_tracker import CostEntry, CostTracker

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status", Static)
        assert "$" not in str(status.render())
        entry = CostEntry(
            timestamp=0.0,
            model="test",
            input_tokens=1000,
            output_tokens=10,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost=0.123,
        )
        app._chat_ctx.run(CostTracker.record, entry)
        app._update_status()
        await pilot.pause()
        assert "$0.12" in str(status.render())


@pytest.mark.asyncio
async def test_inline_queued_view_summarizes_overflow(tmp_path):
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.prompt_queue = [f"prompt {i}" for i in range(8)]
        app._update_queued_view()
        await pilot.pause()
        rendered = str(app.query_one("#queued", Static).render())
        assert rendered.count("⏳ queued") == 4
        assert "+4 more queued" in rendered


@pytest.mark.asyncio
async def test_tool_call_collapsible_has_single_marker(tmp_path):
    """Collapsible draws its own ▶; the title must not add another."""
    content = "Running it:\n```shell\nls\n```"
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        widget = AssistantMessage(content)
        await app.mount(widget)
        await pilot.pause()
        block = widget.query(".tool-call-block").results(Collapsible).__next__()
        assert str(block.title) == "shell: ls"


def test_system_display_text_strips_wrapper_tags():
    from gptme.tui.app import _system_display_text

    assert (
        _system_display_text(
            "<system_info>Working directory changed to: /x</system_info>"
        )
        == "Working directory changed to: /x"
    )
    assert _system_display_text("plain output") == "plain output"
    # only a tag wrapping the whole message is stripped
    assert _system_display_text("a <system_info>b</system_info>") == (
        "a <system_info>b</system_info>"
    )


@pytest.mark.asyncio
async def test_one_line_system_message_not_collapsible(tmp_path):
    manager = make_manager(
        tmp_path, [Message("system", "<system_info>cwd is /x</system_info>")]
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        widget = app.query_one(SystemMessage)
        assert not widget.query(Collapsible)
        assert "cwd is /x" in str(widget.query_one(Static).render())
        assert "system_info" not in str(widget.query_one(Static).render())


@pytest.mark.parametrize("expanded", [False, True])
def test_renderables_one_line_system_message(expanded: bool) -> None:
    msg = Message("system", "<system_info>cwd is /x</system_info>")
    rendered = [str(r) for r in renderables_for_message(msg, expanded=expanded)]
    assert rendered == ["cwd is /x", ""]


@pytest.fixture
def long_single_line_json() -> str:
    """Synthetic gh-style output, without any personal source data."""
    return json.dumps(
        [{"number": i, "title": f"Synthetic result {i}"} for i in range(500)]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [32, 100])
async def test_long_one_line_system_message_collapses(
    tmp_path: Path, long_single_line_json: str, width: int
) -> None:
    content = long_single_line_json
    assert "\n" not in content
    msg = Message("system", content)
    manager = make_manager(tmp_path, [msg])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=(width, 30)) as pilot:
        await pilot.pause()
        widget = app.query_one(SystemMessage)
        block = widget.query_one(Collapsible)
        body = block.query_one(ChatMarkdown)
        assert block.collapsed
        assert str(block.title) == _summarize(content)
        collapsed_height = widget.size.height
        assert collapsed_height <= 5
        # The truncated title must not cause any loss in the expandable body.
        assert body._markdown == content

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert not block.collapsed
        assert widget.size.height > collapsed_height
        assert body._markdown == content

        inp = app.query_one("#input", ChatInput)
        for command, expanded in [
            ("/display outputs off", False),
            ("/display outputs on", True),
        ]:
            inp.text = command
            await pilot.press("enter")
            await pilot.pause()
            assert block.collapsed is not expanded
        assert msg.content == content
        assert manager.log[0].content == content


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [32, 100])
@pytest.mark.parametrize("multiline", [False, True])
async def test_new_system_messages_inherit_display_outputs(
    tmp_path: Path, long_single_line_json: str, width: int, multiline: bool
) -> None:
    """Outputs arriving after a toggle must inherit its current setting."""
    content = (
        "```stdout\nfirst line\nsecond line\n```"
        if multiline
        else long_single_line_json
    )
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(width, 30)) as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        for expanded in (True, False):
            inp.text = f"/display outputs {'on' if expanded else 'off'}"
            await pilot.press("enter")
            await pilot.pause()
            assert app._outputs_expanded is expanded

            msg = Message("system", content)
            app._on_step_message(msg)
            await app.wait_rendered()
            await pilot.pause()
            widget = list(app.query(SystemMessage))[-1]
            assert widget.query_one(Collapsible).collapsed is not expanded
            assert widget.query_one(ChatMarkdown)._markdown == content
            assert msg.content == content
            # Existing outputs must continue following the same toggle.
            assert all(c.collapsed is not expanded for c in app.query(Collapsible))


@pytest.mark.parametrize("width", [32, 100])
@pytest.mark.parametrize("expanded", [False, True])
def test_renderables_long_one_line_system_message(
    long_single_line_json: str, width: int, expanded: bool
) -> None:
    content = long_single_line_json
    msg = Message("system", content)
    renderables = renderables_for_message(msg, expanded=expanded)
    assert renderables[0].plain == "▶ " + _summarize(content)
    output = io.StringIO()
    console = Console(file=output, width=width, color_system=None)
    for renderable in renderables:
        console.print(renderable)
    if expanded:
        body = renderables[1]
        assert isinstance(body, Padding)
        assert isinstance(body.renderable, RichMarkdown)
        assert body.renderable.markup == content
        # Rich can wrap between words at either terminal width.
        assert "Synthetic result 499" in " ".join(output.getvalue().split())
    else:
        assert len(output.getvalue().splitlines()) <= 5
        assert "Synthetic result 499" not in output.getvalue()
    assert msg.content == content


@pytest.mark.parametrize(("length", "collapsible"), [(80, False), (81, True)])
def test_system_message_single_line_summary_boundary(
    length: int, collapsible: bool
) -> None:
    content = "x" * length
    widgets = list(SystemMessage(content).compose())
    assert isinstance(widgets[0], Collapsible) is collapsible
    inline = renderables_for_message(Message("system", content))
    assert inline[0].plain == ("▶ " + _summarize(content) if collapsible else content)


@pytest.mark.parametrize("expanded", [False, True])
def test_renderables_multiline_system_message(expanded: bool) -> None:
    content = "first line\nsecond line"
    renderables = renderables_for_message(Message("system", content), expanded=expanded)
    assert renderables[0].plain == "▶ first line (2 lines)"
    if expanded:
        assert renderables[1].renderable.markup == content
    else:
        assert len(renderables) == 2


@pytest.mark.asyncio
async def test_hidden_step_messages_not_shown(tmp_path):
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_step_message(
            Message("system", "<system_warning>Token usage</system_warning>", hide=True)
        )
        await pilot.pause()
        assert not app.query(SystemMessage)


@pytest.mark.asyncio
async def test_display_hidden_toggles_hidden_messages(tmp_path, monkeypatch):
    monkeypatch.delenv("GPTME_TUI_DISPLAY_HIDDEN", raising=False)
    manager = make_manager(
        tmp_path,
        [
            Message("user", "hi"),
            Message("system", "<system_info>secret notice</system_info>", hide=True),
        ],
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.query(SystemMessage)
        inp = app.query_one("#input", ChatInput)
        inp.text = "/display hidden on"
        await pilot.press("enter")
        await pilot.pause()
        assert app.show_hidden
        assert any(
            "secret notice" in str(s.render())
            for w in app.query(SystemMessage)
            for s in w.query(Static)
        )
        # live hidden messages follow the setting
        app._on_step_message(Message("system", "another notice", hide=True))
        await pilot.pause()
        assert len(app.query(SystemMessage)) >= 2
        inp.text = "/display hidden off"
        await pilot.press("enter")
        await pilot.pause()
        assert not app.query(SystemMessage)


def test_role_labels_use_configured_names(monkeypatch):
    from types import SimpleNamespace

    from gptme.tui import app as tui_app

    config = SimpleNamespace(
        user=SimpleNamespace(user=SimpleNamespace(name="Erik")),
        chat=SimpleNamespace(agent_config=SimpleNamespace(name="Bob")),
    )
    monkeypatch.setattr(tui_app, "get_config", lambda: config)
    assert tui_app._role_label("user") == "Erik"
    assert tui_app._role_label("assistant") == "Bob"
    assert tui_app._role_label("system") == "System"
    rendered = renderables_for_message(Message("assistant", "hi"))
    assert str(rendered[0]) == "Bob"

    config.chat = None
    assert tui_app._role_label("assistant") == "Assistant"


@pytest.mark.asyncio
async def test_long_paste_collapses_to_placeholder(tmp_path):
    from textual import events

    manager = make_manager(tmp_path, [Message("user", "hello")])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.generating = True  # submissions queue, so we can inspect them
        inp = app.query_one("#input", ChatInput)
        pasted = "line 1\nline 2\nline 3"
        app.post_message(events.Paste(pasted))
        await pilot.pause()
        assert inp.text == "[Pasted text #1 +3 lines]"
        await pilot.press("space", "o", "k")
        await pilot.press("enter")
        await pilot.pause()
        assert app.prompt_queue == [f"{pasted} ok"]

        # short pastes are inserted as-is
        app.post_message(events.Paste("short"))
        await pilot.pause()
        assert inp.text == "short"

        # backspace at the end of a placeholder removes the whole token
        inp.text = ""
        app.post_message(events.Paste("x" * 900))
        await pilot.pause()
        assert inp.text == "[Pasted text #1 900 chars]"
        await pilot.press("backspace")
        await pilot.pause()
        assert inp.text == ""


@pytest.mark.asyncio
async def test_paste_placeholder_is_atomic(tmp_path):
    """The cursor can't enter a placeholder; Delete/Backspace remove it whole."""
    from textual import events

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        await pilot.press("a")
        app.post_message(events.Paste("1\n2\n3"))
        await pilot.pause()
        await pilot.press("b")
        label = "[Pasted text #1 +3 lines]"
        assert inp.text == f"a{label}b"
        end = 1 + len(label)
        # moving left from after the token jumps over it
        await pilot.press("left")  # before "b"
        await pilot.press("left")
        assert inp.cursor_location == (0, 1)
        # moving right from before the token jumps over it
        await pilot.press("right")
        assert inp.cursor_location == (0, end)
        # placing the cursor inside snaps to the nearest edge
        inp.move_cursor((0, 3))
        await pilot.pause()
        assert inp.cursor_location == (0, 1)
        # Delete at the start removes the whole token
        await pilot.press("delete")
        await pilot.pause()
        assert inp.text == "ab"


@pytest.mark.asyncio
async def test_paste_placeholder_survives_other_edits(tmp_path):
    """Word/line deletes remove whole tokens; expansion is a single pass."""
    from textual import events

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        await pilot.press("x", "space")
        app.post_message(events.Paste("1\n2\n3"))
        await pilot.pause()
        # a word-delete at the end of the token takes the whole token
        inp.action_delete_word_left()
        await pilot.pause()
        assert inp.text == "x "

        # pasted text containing a later label is not re-expanded
        app.post_message(events.Paste("see [Pasted text #2 +3 lines]\n2\n3"))
        await pilot.pause()
        app.post_message(events.Paste("a\nb\nc"))
        await pilot.pause()
        assert inp._expand_pastes(inp.text) == (
            "x see [Pasted text #2 +3 lines]\n2\n3a\nb\nc"
        )


def test_tui_accepts_prompt_arguments():
    from gptme.tui.main import main
    from gptme.util.multiprompt import group_prompt_args

    params = main.make_context("gptme-tui", ["write it", "-", "test it"]).params
    assert params["prompts"] == ("write it", "-", "test it")
    # same grouping as the CLI
    assert group_prompt_args(params["prompts"]) == ["write it", "test it"]


def test_tui_accepts_option_like_prompts():
    from gptme.tui.main import main

    params = main.make_context("gptme-tui", ["-foo"]).params
    assert params["prompts"] == ("-foo",)


@pytest.mark.asyncio
async def test_initial_prompts_submit_first_and_queue_rest(tmp_path, monkeypatch):
    app = GptmeApp(
        make_manager(tmp_path), workspace=tmp_path, initial_prompts=["one", "two"]
    )
    started: list[bool] = []

    def fake_start() -> None:
        started.append(True)
        app.generating = True

    monkeypatch.setattr(app, "_start_generation", fake_start)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert [m.content for m in app.manager.log if m.role == "user"] == ["one"]
        assert app.prompt_queue == ["two"]
        assert started
        assert app.generating is True
        widgets = app._queued_widgets
        assert len(widgets) == 1
        assert isinstance(widgets[0], UserMessage)
        assert widgets[0].content == "two"


@pytest.mark.asyncio
async def test_rebuild_chat_keeps_queued_prompt_visible(tmp_path, monkeypatch):
    """Queued prompts are TUI-only; rebuilding from the log must remount them."""
    app = GptmeApp(
        make_manager(tmp_path), workspace=tmp_path, initial_prompts=["one", "two"]
    )

    def fake_start() -> None:
        app.generating = True

    monkeypatch.setattr(app, "_start_generation", fake_start)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert app.prompt_queue == ["two"]
        app._rebuild_chat()
        await pilot.pause()
        queued = [w for w in app.query(UserMessage) if "queued" in w.classes]
        assert len(queued) == 1
        assert queued[0].content == "two"
        assert app._queued_widgets == queued
        # popping after a rebuild must remove the remounted widget, not crash
        await app._generation_done()
        await pilot.pause()
        assert app.prompt_queue == []
        # the queued widget stays until the sent message, once rendered,
        # takes its place
        for _ in range(100):
            if not any("queued" in w.classes for w in app.query(UserMessage)):
                break
            await pilot.pause(0.01)
        assert not any("queued" in w.classes for w in app.query(UserMessage))
        assert [m.content for m in app.manager.log if m.role == "user"] == [
            "one",
            "two",
        ]


@pytest.mark.asyncio
async def test_initial_prompts_submit_next_when_turn_finishes(tmp_path, monkeypatch):
    app = GptmeApp(
        make_manager(tmp_path), workspace=tmp_path, initial_prompts=["one", "two"]
    )
    started: list[int] = []

    def fake_start() -> None:
        started.append(1)
        app.generating = True

    monkeypatch.setattr(app, "_start_generation", fake_start)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert app.prompt_queue == ["two"]
        await app._generation_done()
        await pilot.pause()
        assert [m.content for m in app.manager.log if m.role == "user"] == [
            "one",
            "two",
        ]
        assert app.prompt_queue == []
        assert len(started) == 2


@pytest.mark.asyncio
async def test_initial_slash_command_uses_command_handler(tmp_path, monkeypatch):
    app = GptmeApp(
        make_manager(tmp_path),
        workspace=tmp_path,
        initial_prompts=["/model mock/echo", "hello"],
    )
    handled: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(app, "_handle_command", lambda text: handled.append(text))
    monkeypatch.setattr(app, "_start_generation", lambda: started.append("gen"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert handled == ["/model mock/echo"]
        assert [m.content for m in app.manager.log if m.role == "user"] == ["hello"]
        assert started == ["gen"]
        assert app.prompt_queue == []


@pytest.mark.asyncio
async def test_initial_quit_command_does_not_drain_queue(tmp_path, monkeypatch):
    """A queued prompt must not be submitted behind an exiting command."""
    app = GptmeApp(
        make_manager(tmp_path),
        workspace=tmp_path,
        initial_prompts=["/quit", "hello"],
    )
    started: list[str] = []
    monkeypatch.setattr(app, "_start_generation", lambda: started.append("gen"))
    # Don't really tear the app down: /quit must not submit the queued prompt
    # even before the app has finished exiting.
    monkeypatch.setattr(app, "exit", lambda *args, **kwargs: None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
    assert app._quitting is True
    assert app.prompt_queue == ["hello"]
    assert [m.content for m in app.manager.log if m.role == "user"] == []
    assert started == []


@pytest.mark.asyncio
async def test_initial_quit_command_exits_without_submitting_queue(
    tmp_path, monkeypatch
):
    """The real exit path must unmount without submitting a queued prompt."""
    app = GptmeApp(
        make_manager(tmp_path),
        workspace=tmp_path,
        initial_prompts=["/quit", "hello"],
    )
    started: list[str] = []
    monkeypatch.setattr(app, "_start_generation", lambda: started.append("gen"))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.is_running
    assert app._quitting is True
    assert app.prompt_queue == ["hello"]
    assert [m.content for m in app.manager.log if m.role == "user"] == []
    assert started == []


def _make_conv(logs_dir, name):
    (logs_dir / name).mkdir(parents=True)
    (logs_dir / name / "conversation.jsonl").write_text("{}\n")


def test_resume_option_takes_optional_name():
    from gptme.tui.main import main

    def params(args):
        return main.make_context("gptme-tui", args).params

    assert params([])["resume"] is None
    assert params(["--resume"])["resume"] == ""
    assert params(["--resume", "my-conv"])["resume"] == "my-conv"
    # an option after the flag isn't taken as the name
    assert params(["-r", "-m", "mock/echo"])["resume"] == ""


def test_select_logdir(tmp_path, monkeypatch):
    import importlib

    import click

    from gptme.tui import main as tui_main

    cli_main = importlib.import_module("gptme.cli.main")
    monkeypatch.setattr(tui_main, "get_logs_dir", lambda: tmp_path)
    _make_conv(tmp_path, "older")
    _make_conv(tmp_path, "newer")

    select = tui_main._select_logdir
    assert select("random", "older") == tmp_path / "older"
    # CLI spelling: --resume --name older
    assert select("older", "") == tmp_path / "older"
    with pytest.raises(click.UsageError, match="No conversation named"):
        select("random", "missing")
    # bare --resume picks from a list; it never guesses "the latest"
    monkeypatch.setattr(cli_main, "pick_log", lambda: tmp_path / "picked")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert select("random", "") == tmp_path / "picked"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(click.UsageError, match="needs a terminal"):
        select("random", "")


@pytest.mark.asyncio
async def test_paste_placeholder_normalizes_line_endings(tmp_path):
    """Collapsed pastes submit LF line breaks, like TextArea's own insert."""
    from textual import events

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.generating = True  # submissions queue, so we can inspect them
        inp = app.query_one("#input", ChatInput)
        # terminals commonly send CR (or CRLF) line breaks in bracketed paste
        app.post_message(events.Paste("1\r2\r3"))
        await pilot.pause()
        app.post_message(events.Paste("a\r\nb\r\nc"))
        await pilot.pause()
        assert inp.text == "[Pasted text #1 +3 lines][Pasted text #2 +3 lines]"
        await pilot.press("enter")
        await pilot.pause()
        assert app.prompt_queue == ["1\n2\n3a\nb\nc"]


@pytest.mark.asyncio
async def test_paste_placeholder_undo_redo(tmp_path):
    """Undo restores a whole token after a widened delete; redo removes it."""
    from textual import events

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        await pilot.press("x", "space")
        app.post_message(events.Paste("1\n2\n3"))
        await pilot.pause()
        token_text = "x [Pasted text #1 +3 lines]"
        assert inp.text == token_text
        await pilot.press("backspace")
        assert inp.text == "x "
        inp.undo()
        await pilot.pause()
        assert inp.text == token_text
        inp.redo()
        await pilot.pause()
        assert inp.text == "x "
        inp.undo()
        await pilot.pause()
        assert inp._expand_pastes(inp.text) == "x 1\n2\n3"


@pytest.mark.asyncio
async def test_terminal_focus_changes_keep_input_focused(tmp_path):
    """Blur must not drop focus (Textual's default), and focus-in restores it."""
    from textual import events

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        assert not inp.cursor_blink, "steady cursor, like shells"
        focused_line = inp.render_line(0)
        app.post_message(events.AppBlur())
        await pilot.pause()
        assert app.focused is inp
        # still focused, but the rendered cursor is gone while the terminal
        # is unfocused (checks the actual render: TextArea caches lines)
        assert not inp._draw_cursor
        assert inp.render_line(0) != focused_line
        # focus lost some other way: focus-in puts it back on the input
        app.screen.set_focus(None)
        app.post_message(events.AppFocus())
        await pilot.pause()
        assert app.focused is inp
        assert inp._draw_cursor
        assert inp.render_line(0) == focused_line


def test_current_dir_recovers_from_stale_cwd(tmp_path, monkeypatch):
    import click

    from gptme.tui import main as tui_main

    def stale():
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(tui_main.Path, "cwd", staticmethod(stale))
    monkeypatch.setattr(tui_main.os, "chdir", lambda p: None)
    monkeypatch.setenv("PWD", str(tmp_path))
    assert tui_main._current_dir() == tmp_path
    monkeypatch.setenv("PWD", str(tmp_path / "gone"))
    with pytest.raises(click.ClickException, match="no longer exists"):
        tui_main._current_dir()


def test_drop_summary_line():
    from gptme.tui.app import _drop_summary_line

    content = "Shellcheck found potential issues:\n\n```\nSC2044 warning\n```\n"
    assert _drop_summary_line(content) == "```\nSC2044 warning\n```"
    # a leading fence is not the title, keep it
    fenced = "```stdout\nhello\n```"
    assert _drop_summary_line(fenced) == fenced
    # nothing but the summary line: keep the content
    assert _drop_summary_line("just one line") == "just one line"


def test_tool_title_highlighting():
    from gptme.tui.app import _summary_title, _tool_title

    title = _tool_title("▶ shell: ls -la", "bash", highlight=True)
    assert title.plain == "▶ shell: ls -la"
    assert title.spans, "expected highlight styles"
    # Collapsible draws its own marker
    assert _tool_title("▶ shell: ls", "bash", True, marker=False).plain == "shell: ls"
    plain = _tool_title("▶ shell: ls", "bash", highlight=False)
    assert plain.plain == "▶ shell: ls" and not plain.spans

    summary = _summary_title("Ran command: `ls -la` (3 lines)", highlight=True)
    assert summary.plain == "Ran command: ls -la (3 lines)"
    assert summary.spans
    # unmatched backtick stays literal
    assert _summary_title("a `b", highlight=True).plain == "a `b"


def test_tool_format_title_shows_path_not_json():
    title, code, lang = _tool_call_renderable(
        '@save(call_1): {"path": "/tmp/x.py", "content": "print(1)"}'
    )
    assert title == "▶ save: /tmp/x.py"
    # the expanded body is the content to be saved, highlighted as its file type
    assert code == "print(1)"
    assert lang == "python"


def test_patch_renders_as_diff_in_all_formats():
    patch = "<<<<<<< ORIGINAL\nold line\n=======\nnew line\n>>>>>>> UPDATED"
    import json

    tool_call = "@patch(c1): " + json.dumps({"path": "a.py", "patch": patch})
    title, code, lang = _tool_call_renderable(tool_call)
    assert title == "▶ patch: a.py"
    assert lang == "diff"
    assert "-old line" in code and "+new line" in code

    title, code, lang = _markdown_tool_renderable(f"```patch a.py\n{patch}\n```")
    assert (title, lang) == ("▶ patch: a.py", "diff")
    assert "-old line" in code and "+new line" in code

    title, code, lang = _markdown_tool_renderable("```save b.md\n# Hi\n```")
    assert (title, code) == ("▶ save: b.md", "# Hi")
    assert lang == "markdown"


def test_patch_many_renders_per_file_diffs():
    import json

    patch = "<<<<<<< ORIGINAL\na\n=======\nb\n>>>>>>> UPDATED"
    patches = json.dumps(
        [{"path": "x.py", "patch": patch}, {"path": "y.py", "patch": patch}]
    )
    tool_call = "@patch_many(c1): " + json.dumps({"patches": patches})
    title, code, lang = _tool_call_renderable(tool_call)
    assert title == "▶ patch_many: x.py (+1 more)"
    assert lang == "diff"
    assert "--- x.py" in code and "--- y.py" in code and "+b" in code


def test_patch_many_empty_patches_does_not_crash():
    """An empty patches list must still render instead of IndexError."""
    title, code, lang = _tool_call_renderable('@patch_many(c1): {"patches": []}')
    assert title == "▶ patch_many"
    assert code == ""
    assert lang == "diff"


def test_patch_preview_skips_placeholders():
    """`# ...` separators are not file edits; don't show them as -/+ lines."""
    import json

    patch = (
        "<<<<<<< ORIGINAL\n"
        "old\n"
        "    # ...\n"
        "tail\n"
        "=======\n"
        "new\n"
        "    # ...\n"
        "tail\n"
        ">>>>>>> UPDATED"
    )
    title, code, lang = _tool_call_renderable(
        "@patch(c1): " + json.dumps({"path": "a.py", "patch": patch})
    )
    assert title == "▶ patch: a.py"
    assert lang == "diff"
    assert "-old" in code and "+new" in code
    assert "# ..." not in code


def test_tool_format_title_non_string_path():
    """A numeric path must still render instead of crashing the TUI."""
    title, code, _lang = _tool_call_renderable(
        '@save(call_1): {"path": 123, "content": "print(1)"}'
    )
    assert title == "▶ save: 123"
    assert code == "print(1)"


@pytest.mark.asyncio
async def test_display_highlight_and_toggle_refocus(tmp_path):
    manager = make_manager(
        tmp_path, [Message("system", "Ran command: `ls`\n```stdout\na\n```")]
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/display highlight off"
        await pilot.press("enter")
        await pilot.pause()
        assert not app.highlight
        # clicking a section title toggles it; focus goes back to the input
        title = app.query_one(SystemMessage).query_one(Collapsible)
        await pilot.click(title)
        await pilot.pause()
        assert not title.collapsed
        assert app.focused is inp


def test_configured_role_color_precedence(monkeypatch):
    from types import SimpleNamespace

    from gptme import constants

    config = SimpleNamespace(
        user=SimpleNamespace(user=SimpleNamespace(color="magenta")),
        chat=SimpleNamespace(agent_config=SimpleNamespace(color="#e5a50a")),
    )
    monkeypatch.setattr("gptme.config.get_config", lambda: config)
    monkeypatch.delenv("GPTME_AGENT_COLOR", raising=False)
    assert constants.configured_role_color("user") == "magenta"
    assert constants.configured_role_color("assistant") == "#e5a50a"
    assert constants.configured_role_color("system") is None
    monkeypatch.setenv("GPTME_AGENT_COLOR", "red")
    assert constants.configured_role_color("assistant") == "red"
    config.chat = None
    monkeypatch.delenv("GPTME_AGENT_COLOR")
    assert constants.role_color("assistant") == "green"


@pytest.mark.asyncio
async def test_messages_use_configured_role_color(tmp_path, monkeypatch):
    colors = {"assistant": "#e5a50a"}
    monkeypatch.setattr(
        "gptme.tui.app.configured_role_color", lambda role: colors.get(role)
    )
    manager = make_manager(
        tmp_path, [Message("user", "hi"), Message("assistant", "hello")]
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assistant = app.query_one(AssistantMessage)
        assert assistant.styles.border_left[1].hex == "#E5A50A"
        assert assistant.query_one(".role").styles.color.hex == "#E5A50A"
        # no color configured for the user: theme default kept
        user = app.query_one(UserMessage)
        assert user.styles.border_left[1].hex != "#E5A50A"


def test_drop_summary_line_keeps_truncated_first_line():
    """A first line cut short in the title must stay in the expanded body."""
    from gptme.tui.app import SUMMARY_MAXLEN, _drop_summary_line

    long_first = "x" * (SUMMARY_MAXLEN + 5)
    content = f"{long_first}\nmore"
    assert _drop_summary_line(content) == content


def test_restart_not_attempted_when_workspace_is_gone(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from gptme.tools import restart
    from gptme.tui import main as tui_main

    calls: list[str] = []
    monkeypatch.setattr(restart, "_do_restart", lambda name: calls.append(name))
    app = SimpleNamespace(
        end_session=lambda: None,
        restart_requested=True,
        restart_web_url=None,
        restart_target=None,
        workspace=tmp_path / "gone",
    )
    tui_main._finish_session(app, "conv")  # type: ignore[arg-type]
    assert calls == []
    assert "Not restarting" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "expected"), [("y", ["https://example.com"]), ("n", [])]
)
async def test_url_prompt_asks_before_fetching(tmp_path, monkeypatch, key, expected):
    """Typing a URL opens the confirm dialog from the real input handler.

    Regression: _submit used push_screen_wait, which raises NoActiveWorker
    outside a worker, so any prompt containing a URL crashed the TUI.
    """
    from gptme.tui.app import UrlConfirmScreen

    seen: list[list[str] | None] = []

    def fake_include_paths(msg, workspace, pre_confirmed_urls=None):
        seen.append(pre_confirmed_urls)
        return msg

    monkeypatch.setattr("gptme.tui.app.include_paths", fake_include_paths)
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    monkeypatch.setattr(app, "_start_generation", lambda: None)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#input", ChatInput).text = "look at https://example.com"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, UrlConfirmScreen)
        assert seen == []  # nothing sent before the user answers
        await pilot.press(key)
        await pilot.pause()
        assert not isinstance(app.screen, UrlConfirmScreen)
    assert seen == [expected]
    assert app.manager.log[-1].content == "look at https://example.com"


@pytest.mark.asyncio
async def test_click_on_just_removed_widget_does_not_crash(tmp_path, monkeypatch):
    """A click can hit a widget removed since the last frame (e.g. the
    "Generating…" placeholder dropped on interrupt): Textual then starts a
    text selection in its parent, which is None, and crashes the app."""
    from textual import events
    from textual.geometry import Offset

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        detached = Static("Generating…", classes="progress-placeholder")
        assert detached.parent is None
        monkeypatch.setattr(
            app.screen,
            "get_widget_and_offset_at",
            lambda x, y: (detached, Offset(0, 0)),
        )
        # post like the driver does: Pilot.click bypasses App.on_event
        app.post_message(events.MouseDown(None, 5, 5, 0, 0, 1, False, False, False))
        await pilot.pause()
        assert app.is_running


@pytest.mark.asyncio
async def test_slow_command_does_not_block_ui(tmp_path, monkeypatch):
    """Commands run in a worker: a slow one (e.g. /compact calling the LLM)
    must not freeze the UI; input typed meanwhile queues and is sent after."""
    import threading

    entered = threading.Event()
    release = threading.Event()

    def slow_cmd(msg, manager):
        entered.set()
        assert release.wait(5)
        print("compacted")
        return True

    monkeypatch.setattr("gptme.tui.app.execute_cmd", slow_cmd)
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    started: list[bool] = []
    monkeypatch.setattr(app, "_start_generation", lambda: started.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._interrupt_event.set()  # left over from an earlier interrupted turn
        inp = app.query_one("#input", ChatInput)
        inp.text = "/compact resume"
        await pilot.press("enter")
        await pilot.pause()
        assert entered.wait(5)
        assert app.state == "running /compact…"
        # the event loop is free while the command runs: typing queues
        inp.text = "next prompt"
        await pilot.press("enter")
        await pilot.pause()
        assert app.prompt_queue == ["next prompt"]
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.state == "idle"
        infos = [str(i.render()) for i in app.query(InfoMessage)]
        assert any("compacted" in i for i in infos), infos
        assert app.prompt_queue == []
        assert started == [True]
        assert app.manager.log[-1].content == "next prompt"


@pytest.mark.asyncio
async def test_interrupt_during_command_returns_queued_input(tmp_path, monkeypatch):
    """Ctrl+C during a slow command hands queued text back to the input,
    as after an interrupted turn, instead of sending it."""
    import threading

    entered = threading.Event()
    release = threading.Event()

    def slow_cmd(msg, manager):
        entered.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr("gptme.tui.app.execute_cmd", slow_cmd)
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    started: list[bool] = []
    monkeypatch.setattr(app, "_start_generation", lambda: started.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/compact resume"
        await pilot.press("enter")
        assert entered.wait(5)
        inp.text = "draft"
        await pilot.press("enter")
        await pilot.pause()
        app.action_interrupt()
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert started == []
        assert app.prompt_queue == []
        assert inp.text == "draft"


@pytest.mark.asyncio
async def test_exit_command_does_not_submit_queued_prompt(tmp_path, monkeypatch):
    """A prompt queued behind /exit must not be sent while the app shuts down."""

    def exit_cmd(msg, manager):
        raise SystemExit

    monkeypatch.setattr("gptme.tui.app.execute_cmd", exit_cmd)
    app = GptmeApp(
        make_manager(tmp_path), workspace=tmp_path, initial_prompts=["/exit", "hello"]
    )
    started: list[bool] = []
    monkeypatch.setattr(app, "_start_generation", lambda: started.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert started == []
    assert [m.content for m in app.manager.log if m.role == "user"] == ["/exit"]


@pytest.mark.asyncio
async def test_stream_updates_are_batched(tmp_path, monkeypatch):
    """Chunks from the worker are buffered and rendered at most STREAM_FPS
    times a second: each render re-lays out the whole buffer."""
    import threading
    import time

    from gptme.tui.app import STREAM_FPS

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    renders: list[str] = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app._begin_stream()
        await pilot.pause()
        widget = app._stream_widget
        assert widget is not None
        real_append = widget.append_token

        def counting_append(token: str) -> None:
            renders.append(token)
            real_append(token)

        monkeypatch.setattr(widget, "append_token", counting_append)

        n, interval = 200, 0.005  # paced: ~1s of chunks while the UI runs

        def stream() -> None:
            for i in range(n):
                app._on_token(f"{i} ")
                time.sleep(interval)

        start = time.monotonic()
        worker = threading.Thread(target=stream)
        worker.start()
        while worker.is_alive():
            await pilot.pause(0.02)
        elapsed = time.monotonic() - start
        await pilot.pause(0.2)
        assert "".join(renders) == "".join(f"{i} " for i in range(n))
        # one render per chunk would be ~200; the cap allows ~STREAM_FPS/s
        assert 2 <= len(renders) <= elapsed * STREAM_FPS + 3, (len(renders), elapsed)


@pytest.mark.asyncio
async def test_chat_anchors_only_once_overflowing(tmp_path):
    """A short chat starts at the top; once it overflows it is anchored to
    the bottom instead of scrolling on every update."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat")
        app._show_info("short")
        await pilot.pause()
        assert not chat.is_anchored
        for i in range(30):
            app._show_info(f"line {i}")
        await pilot.pause()
        assert chat.is_anchored
        assert chat.scroll_y == chat.max_scroll_y


@pytest.mark.asyncio
async def test_tool_results_mount_above_persistent_status_line(tmp_path):
    """Tool results go above the 'Running tool…' line instead of removing
    and remounting it after every result."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._show_tool_placeholder()
        placeholder = app._tool_placeholder
        app.state = "executing tools"
        app._on_step_message(Message("system", "result 1"))
        app._on_step_message(Message("system", "result 2"))
        await pilot.pause()
        assert app._tool_placeholder is placeholder
        chat = app.query_one("#chat")
        assert chat.children[-1] is placeholder


@pytest.mark.asyncio
async def test_statusline_command_renders_payload(tmp_path):
    """[tui] statusline: the command gets the JSON payload on stdin and its
    (ANSI) output replaces the built-in status."""
    import shlex

    script = tmp_path / "statusline.py"
    script.write_text(
        "import json, sys\n"
        "d = json.load(sys.stdin)\n"
        "g = d['gptme']\n"
        "print(f\"\\033[35m{d['session_name']}\\033[0m turns={g['turns']} "
        "state={g['state']} cwd={d['workspace']['current_dir']}\")\n"
        "print('second line')\n"
    )
    app = GptmeApp(
        make_manager(tmp_path, [Message("user", "hi"), Message("assistant", "yo")]),
        workspace=tmp_path,
    )
    app._statusline_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        status = app.query_one("#status", Static)
        text = str(status.render())
        assert "test-conversation turns=1 state=idle" in text, text
        assert f"cwd={tmp_path}" in text
        assert "second line" in text
        assert "\x1b" not in text  # ANSI parsed into styles, not shown raw


@pytest.mark.asyncio
async def test_failing_statusline_falls_back_to_builtin(tmp_path):
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    app._statusline_cmd = "exit 3"
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "idle" in str(app.query_one("#status", Static).render())


def test_statusline_log_stats():
    from gptme.tui.statusline import LogStats

    log = [
        Message("user", "hi"),
        Message("assistant", "```shell\nls\n```"),
        Message("system", "ok"),
        Message("user", "/compact"),
        Message("assistant", "```shell\npwd\n```\n```save x.txt\nx\n```"),
    ]
    stats = LogStats()
    stats.update(log)
    assert (stats.turns, stats.steps) == (1, 2)
    assert stats.tools == {"shell": 2, "save": 1}
    stats.update(log[:2])  # rewritten (e.g. undo): recount
    assert (stats.turns, stats.steps) == (1, 1)


def test_statusline_timeout_kills_command_and_bad_output_is_tolerated(
    tmp_path, monkeypatch
):
    """A hung command is killed with its whole process group; undecodable
    output doesn't raise (that would stop all later refreshes)."""
    import shlex
    import time

    from gptme.tui import statusline

    monkeypatch.setattr(statusline, "TIMEOUT", 0.5)
    marker = tmp_path / "survived"
    child = f"import time; time.sleep(1.5); open({str(marker)!r}, 'w')"
    cmd = f"{shlex.quote(sys.executable)} -c {shlex.quote(child)}; true"
    assert statusline.run_statusline(cmd, {}, tmp_path) is None
    time.sleep(1.5)
    assert not marker.exists()

    bad = f"{shlex.quote(sys.executable)} -c \"import sys; sys.stdout.buffer.write(b'ok \\xff')\""
    assert statusline.run_statusline(bad, {}, tmp_path) == "ok \ufffd"


def test_statusline_payload_without_model():
    """No model yet: a clear placeholder, not a bare None for scripts to print."""
    from pathlib import Path

    from gptme.tui.statusline import LogStats, build_payload

    payload = build_payload(
        session="s",
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
    assert payload["model"] == {"id": None, "display_name": "<no model set>"}


@pytest.mark.asyncio
async def test_statusline_result_after_shutdown_is_ignored(tmp_path):
    """A statusline run finishing as the app shuts down must not raise."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.screen.query_one("#status").remove()
        app._show_statusline("late output")
        app._show_statusline(None)


def test_streaming_native_tool_call_is_collapsed():
    """A native-format call (``@tool(id): {json}``) streams as a one-line stub,
    not raw JSON, including while its arguments are still arriving."""
    from gptme.tui.app import _streaming_all_segments, _streaming_tool_stub

    buf = (
        "Off-by-one in the windowing. Let me fix it.\n"
        '@ipython(call_38c6): {"code":"\\nimport subprocess, os\\nprint(1)'
    )
    segments = _streaming_all_segments(buf)
    assert [kind for kind, _ in segments] == ["prose", "tool_call"]
    assert _streaming_tool_stub(segments[1][1]) == "▶ ipython: import subprocess, os…"
    assert (
        _streaming_tool_stub('@shell(c1): {"command":"ls -la"}') == "▶ shell: ls -la…"
    )
    assert _streaming_tool_stub("@save(c2): {") == "▶ save…"
    assert _streaming_all_segments("use @mention in prose") == [
        ("prose", "use @mention in prose")
    ]


@pytest.mark.asyncio
async def test_markdown_headers_are_compact_bold_and_left_aligned(tmp_path):
    """Headers in messages: bold, left aligned, no underline, one blank line
    above (none at the top of a message) and none below."""
    from textual.widget import Widget
    from textual.widgets._markdown import MarkdownHeader

    content = "# Title\n\nFirst paragraph.\n\n## Section\n\nSecond paragraph."
    manager = make_manager(tmp_path, [Message("assistant", content)])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        msg = app.query_one(AssistantMessage)
        h1, h2 = msg.query(MarkdownHeader)
        for header in (h1, h2):
            style = header.styles.text_style
            assert style.bold and not style.underline
            assert header.styles.content_align_horizontal == "left"
            assert header.styles.margin.bottom == 0
        assert h1.styles.margin.top == 0
        assert h2.styles.margin.top == 1
        markdown = h1.parent
        assert isinstance(markdown, Widget)
        # the title is the message body's first line, its paragraph right below
        first_paragraph = markdown.children[1]
        assert h1.region.y == markdown.content_region.y
        assert first_paragraph.region.y == h1.region.bottom
        # one blank line between a paragraph and the next header
        assert h2.region.y == first_paragraph.region.bottom + 1


@pytest.mark.asyncio
async def test_take_steer_prompts(tmp_path):
    """Queued prompts join the running turn in order, up to the first one
    that must wait for the turn to end (command, URL, skill invocation)."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        for prompt in ["first", "second", "/compact", "after command"]:
            app._enqueue_prompt(prompt)
        taken = app._take_steer_prompts()
        await pilot.pause()
        assert [m.content for m in taken] == ["first", "second"]
        assert app.prompt_queue == ["/compact", "after command"]
        assert len(app._queued_widgets) == 2
        assert [
            str(w.content) for w in app.query(UserMessage) if not w.has_class("queued")
        ] == [
            "first",
            "second",
        ]

        # URLs not yet confirmed (e.g. initial prompts) wait; queued ones were
        # confirmed when queued and join with exactly the confirmed URLs
        app.prompt_queue = ["see https://example.com"]
        assert app._take_steer_prompts() == []
        confirmed = Message("user", "see https://example.com", quiet=True)
        app._confirmed_urls[id(confirmed)] = []
        app.prompt_queue = [confirmed]
        assert [m.content for m in app._take_steer_prompts()] == [
            "see https://example.com"
        ]
        assert app._confirmed_urls == {}
        app.prompt_queue = [Message("user", "skill prompt")]
        assert app._take_steer_prompts() == []
        app.prompt_queue = ["after interrupt"]
        app._interrupt_event.set()
        assert app._take_steer_prompts() == []


@pytest.mark.asyncio
async def test_queued_prompts_are_sent_together_after_a_turn(tmp_path, monkeypatch):
    """Prompts still queued when a turn ends go in one new turn, not one per
    turn; command-line prompts keep running one turn each."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    started: list[bool] = []
    monkeypatch.setattr(app, "_start_generation", lambda: started.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._enqueue_prompt("first")
        app._enqueue_prompt("second")
        await app._dispatch_queue()
        await pilot.pause()
        assert [m.content for m in app.manager.log if m.role == "user"] == [
            "first",
            "second",
        ]
        assert started == [True]
        assert app.prompt_queue == []


@pytest.mark.asyncio
async def test_command_line_prompts_are_not_steered(tmp_path, monkeypatch):
    app = GptmeApp(
        make_manager(tmp_path), workspace=tmp_path, initial_prompts=["one", "two"]
    )
    monkeypatch.setattr(app, "_start_generation", lambda: None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert app.prompt_queue == ["two"]
        assert app._take_steer_prompts() == []


@pytest.mark.asyncio
async def test_url_dialog_answered_after_interrupt_returns_text(tmp_path, monkeypatch):
    """A URL dialog left open when the turn was interrupted must not start a
    new turn once answered: the text goes back to the input."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    started: list[bool] = []
    monkeypatch.setattr(app, "_start_generation", lambda: started.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._interrupt_event.set()  # interrupted while the dialog was open
        app._enqueue_confirmed("see https://example.com", [])
        await pilot.pause()
        assert started == []
        assert app.query_one("#input", ChatInput).text == "see https://example.com"
        assert app._confirmed_urls == {}


@pytest.mark.asyncio
async def test_compact_summarizes_whole_log_and_rerenders(tmp_path, monkeypatch):
    """/compact summarizes by default, keeps the last message, and the chat
    shows the compacted log afterwards."""
    from gptme.logmanager import Log
    from gptme.tools import init_tools

    init_tools(["autocompact"])
    history = [
        Message("user", "old question"),
        Message("assistant", "old answer"),
        Message("user", "last question"),
        Message("assistant", "last answer"),
    ]
    seen: list[list[Message]] = []

    def fake_resume(mgr, msgs, **kwargs):
        seen.append(list(msgs))
        mgr.log = Log([Message("assistant", "CHECKPOINT"), *msgs[-1:]])
        mgr.write()
        yield Message("system", "compacted")
        return True, "summarize"

    monkeypatch.setattr("gptme.tools.autocompact.handlers._resume_via_llm", fake_resume)
    app = GptmeApp(make_manager(tmp_path, history), workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("#input", ChatInput)
        inp.text = "/compact"
        await pilot.press("enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if not app.generating:
                break
        await pilot.pause()
        assert seen and [m.content for m in seen[0]] == [m.content for m in history]
        shown = [str(w.content) for w in app.query(AssistantMessage)]
        assert any("CHECKPOINT" in s for s in shown), shown
        assert not any("old answer" in s for s in shown), shown
        assert any("last answer" in s for s in shown), shown


@pytest.mark.asyncio
async def test_thinking_and_tool_call_are_compact_and_aligned(tmp_path):
    """A message that only thinks and calls a tool is three rows: the name,
    the thinking line, the tool line; the two lines start in the same column
    (no border line, padding row or extra indent around the tool call)."""
    from textual.widgets import Collapsible

    from gptme.tools import init_tools

    init_tools(["shell"])
    content = "<think>\nlet me look\n</think>\n```shell\ncd ~/\n```"
    manager = make_manager(tmp_path, [Message("assistant", content)])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        msg = app.query_one(AssistantMessage)
        thinking, tool = msg.query(Collapsible)
        assert thinking.has_class("thinking-block")
        assert tool.has_class("tool-call-block")
        assert msg.content_region.height == 3
        assert tool.region.y == thinking.region.bottom
        title = lambda c: c.query_one("CollapsibleTitle").content_region.x  # noqa: E731
        assert title(tool) == title(thinking)
        assert tool.styles.background.a == 0  # no gray box


@pytest.mark.asyncio
async def test_take_steer_prompts_inline(tmp_path):
    """Inline mode has no chat widget: delivering queued prompts mid-turn
    (also with a tool status running) must not touch it."""
    from gptme.tools.base import ToolUse
    from gptme.tui.app import _RunningTool

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_tool_start(_RunningTool.from_tool_use(ToolUse("shell", [], "ls")))
        for prompt in ["first", "second"]:
            app._enqueue_prompt(prompt)
        taken = app._take_steer_prompts()
        assert [m.content for m in taken] == ["first", "second"]
        assert app.prompt_queue == []


@pytest.mark.asyncio
async def test_tool_status_line_is_one_row_for_long_commands(tmp_path):
    """A long command is cut with an ellipsis, not wrapped: the status line
    keeps the height of the stream that replaces it."""
    from gptme.tools.base import ToolUse
    from gptme.tui.app import _RunningTool

    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        app._on_tool_start(
            _RunningTool.from_tool_use(ToolUse("shell", [], "echo " + "x" * 200))
        )
        await app.wait_rendered()
        await pilot.pause()
        placeholder = app.query_one(ToolPlaceholder)
        assert placeholder._body.region.height == 1


def _turn_history() -> list[Message]:
    """A turn of small steps: think + call, result, ... as in the log."""
    step = "<think>\nlook\n</think>\n```shell\n{}\n```"

    def result(text: str, index: int = 0) -> Message:
        return Message(
            "system", f"Ran command: `{text}`", metadata={"tool_call": index}
        )

    return [
        Message("user", "check the repo"),
        Message("assistant", step.format("ls")),
        result("ls"),
        Message("assistant", step.format("git status")),
        result("git status"),
        Message("assistant", "All clean."),
    ]


def _visible_names(app) -> list[str]:
    return [
        str(role.render())
        for role in app.query(".message > .role")
        if role.parent.display and role.display
    ]


@pytest.mark.asyncio
async def test_turn_is_one_block_with_results_under_steps(tmp_path):
    """Grouped (default): the agent's name once per turn, later steps without
    a name or blank line, tool results indented under their step."""
    app = GptmeApp(make_manager(tmp_path, _turn_history()), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        names = _visible_names(app)
        assert len(names) == 2  # the user, then the agent once
        steps = list(app.query(AssistantMessage))
        assert not steps[0].has_class("turn-step")
        assert all(s.has_class("turn-step") for s in steps[1:])
        results = list(app.query(SystemMessage))
        assert all(r.has_class("turn-result") for r in results)
        # indented under the step's call, and no blank line in between
        assert results[0].content_region.x > steps[0].content_region.x
        assert steps[0] in results[0].ancestors
        assert results[0].region.bottom == steps[0].region.bottom
        assert steps[1].region.y == steps[0].region.bottom


def _two_call_step(*, results: list[Message]) -> list[Message]:
    """A step calling two tools, then their results (as in the log)."""
    step = (
        "<think>\nlook around\n</think>\n"
        "```shell\nsed -n 1,10p a.py\n```\n\n```shell\nls hooks/\n```"
    )
    return [
        Message("user", "look around"),
        Message("assistant", step),
        *results,
        Message("assistant", "Found it."),
    ]


def _ran(command: str, call: int | None) -> Message:
    metadata: MessageMetadata = {} if call is None else {"tool_call": call}
    return Message(
        "system", f"Ran command: `{command}`\n\nout 1\nout 2", metadata=metadata
    )


def _call_titles(step) -> list:
    return list(step.query(".tool-call-block"))


@pytest.mark.asyncio
async def test_results_go_under_the_call_they_came_from(tmp_path):
    """A step with two tool calls: each result under its own call, not both
    after the last call."""
    history = _two_call_step(
        results=[_ran("sed -n 1,10p a.py", 0), _ran("ls hooks/", 1)]
    )
    app = GptmeApp(make_manager(tmp_path, history), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        step = app.query(AssistantMessage).first()
        first_call, second_call = _call_titles(step)
        first, second = list(app.query(SystemMessage))
        assert "sed" in first.content and "ls hooks" in second.content
        assert first_call.region.bottom == first.region.y
        assert first.region.bottom == second_call.region.y
        assert second_call.region.bottom == second.region.y
        # indented under the call, in the step's bar
        assert first.content_region.x > first_call.content_region.x
        assert not first.styles.border_left[0]


@pytest.mark.asyncio
async def test_result_from_before_call_index_goes_under_its_call_by_id(tmp_path):
    """Logs from before results named their call's index: a tool-format
    result still finds its call by the call's ID."""
    calls = "\n".join(
        f'@shell({cid}): {{"command": "{cmd}"}}'
        for cid, cmd in (("c1", "ls"), ("c2", "pwd"))
    )
    history = [
        Message("user", "look"),
        Message("assistant", calls),
        Message("system", "Ran command: `ls`\n\na\nb", call_id="c1"),
        Message("system", "Ran command: `pwd`\n\n/x\n/y", call_id="c2"),
    ]
    app = GptmeApp(
        make_manager(tmp_path, history), workspace=tmp_path, tool_format="tool"
    )
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        step = app.query(AssistantMessage).last()
        first_call, second_call = _call_titles(step)
        first, second = list(step.query(SystemMessage))
        assert first_call.region.bottom == first.region.y
        assert second_call.region.bottom == second.region.y


@pytest.mark.asyncio
async def test_results_of_mixed_call_formats_go_under_the_step(tmp_path):
    """Tool-format calls mixed with a markdown block run markdown first, not
    in the order shown: their results go under the step, not a wrong call."""
    content = (
        '@shell(c1): {"command": "ls"}\n\n```shell\npwd\n```\n\n'
        '@shell(c2): {"command": "id"}'
    )
    history = [
        Message("user", "look"),
        Message("assistant", content),
        _ran("pwd", 0),
        _ran("ls", 1),
        _ran("id", 2),
    ]
    app = GptmeApp(
        make_manager(tmp_path, history), workspace=tmp_path, tool_format="tool"
    )
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        chat = app.query_one("#chat")
        results = list(app.query(SystemMessage))
        assert len(results) == 3
        assert all(r.parent is chat and r.has_class("turn-result") for r in results)


@pytest.mark.asyncio
async def test_result_not_matched_to_a_call_goes_under_the_step(tmp_path):
    """A result naming a call the step doesn't show (e.g. one its text
    doesn't render as a call) goes after the step, indented the same as one
    under its call."""
    history = _two_call_step(results=[_ran("sed -n 1,10p a.py", 0)])
    history += [
        Message("user", "again"),
        Message("assistant", "```shell\nls\n```"),
        _ran("ls", 3),  # no such call
    ]
    app = GptmeApp(make_manager(tmp_path, history), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        chat = app.query_one("#chat")
        under_call, unmatched = list(app.query(SystemMessage))
        assert under_call.parent is not chat
        assert unmatched.parent is chat and unmatched.has_class("turn-result")
        assert unmatched.content_region.x == under_call.content_region.x


@pytest.mark.asyncio
async def test_final_step_replacing_the_streamed_one_keeps_results(tmp_path):
    """The step shown when its tools started (streamed) is replaced by the
    final message after they ran: results stay under their calls."""
    history = _two_call_step(results=[])[:2]
    app = GptmeApp(make_manager(tmp_path, history), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        provisional = app.query(AssistantMessage).last()
        app._provisional_assistant = provisional
        app._show_message(_ran("sed -n 1,10p a.py", 0))
        app._show_message(_ran("ls hooks/", 1))
        await app.wait_rendered()
        assert len(provisional.query(SystemMessage)) == 2
        # the final content differs (e.g. trailing text the stream hadn't shown)
        final_msg = Message("assistant", provisional.content + "\n\nDone.")
        app._on_step_message(final_msg)
        await app.wait_rendered()
        await pilot.pause()
        assert not provisional.is_attached
        step = app.query(AssistantMessage).last()
        assert "Done." in step.content
        first_call, second_call = _call_titles(step)
        first, second = list(step.query(SystemMessage))
        assert first_call.region.bottom == first.region.y
        assert second_call.region.bottom == second.region.y
        # and the next result in the turn still finds the final step
        assert app._turn_step is step


@pytest.mark.asyncio
async def test_hook_output_in_a_turn_keeps_its_place_and_block(tmp_path):
    """Output not from a tool call (e.g. a hook) between a step and its result
    stays where it is, in its own block: nothing is pulled out of order."""
    history = _turn_history()
    history.insert(2, Message("system", "Lint hook: 2 warnings"))
    app = GptmeApp(make_manager(tmp_path, history), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        hook, result, *_ = list(app.query(SystemMessage))
        assert "Lint hook" in hook.content
        assert not hook.has_class("turn-result") and not result.has_class("turn-result")
        assert hook.region.y < result.region.y  # log order
        # the block was interrupted: the next step names the agent again
        steps = list(app.query(AssistantMessage))
        assert not steps[1].has_class("turn-step")
        assert steps[2].has_class("turn-step")  # and continues from there


@pytest.mark.asyncio
async def test_display_grouping_off_shows_one_block_per_message(tmp_path):
    app = GptmeApp(make_manager(tmp_path, _turn_history()), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        app._display_command(["grouping", "off"])
        await app.wait_rendered()
        await pilot.pause()
        assert not app.query(".turn-step") and not app.query(".turn-result")
        assert len(_visible_names(app)) == 4  # the user, each agent step
        app._display_command(["grouping", "on"])
        await app.wait_rendered()
        await pilot.pause()
        assert len(_visible_names(app)) == 2


@pytest.mark.asyncio
async def test_display_change_during_a_turn_waits_for_it_to_end(tmp_path):
    """Re-rendering the chat mid-turn would remove the live stream widget from
    under the turn: /display turns/hidden/highlight apply when it ends."""
    app = GptmeApp(make_manager(tmp_path, _turn_history()), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        app.generating = True
        app._begin_stream()
        app._on_stream_token("streaming reply")
        await app.wait_rendered()
        stream = app._stream_widget
        app._display_command(["grouping", "off"])
        await pilot.pause()
        assert stream is not None and stream.is_attached  # untouched
        assert app.query(".turn-step")  # not re-rendered yet
        app._show_info("Error: provider overloaded", error=True)  # ends the turn
        app.generating = False
        app._clear_stream_view()
        await app._generation_done()
        await app.wait_rendered()
        await pilot.pause()
        assert not app.query(".turn-step") and not app.query(".turn-result")
        # notices from the turn (not in the log) survive the re-render
        assert any(
            "provider overloaded" in str(w.render()) for w in app.query(InfoMessage)
        )


@pytest.mark.asyncio
async def test_agent_text_stands_out_among_thinking_and_tool_calls(tmp_path):
    """The agent's own text is set apart from tool lines by a blank line (not
    from the thinking line above it); tool titles are muted like thinking."""
    from textual.widgets import Collapsible

    from gptme.tools import init_tools

    init_tools(["shell"])
    content = "<think>\nhm\n</think>\nI'll check the files.\n```shell\nls\n```"
    manager = make_manager(tmp_path, [Message("assistant", content)])
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=(80, 20)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        msg = app.query_one(AssistantMessage)
        thinking, tool = msg.query(Collapsible)
        prose = msg.query_one("AssistantMessage > ChatMarkdown", ChatMarkdown)
        assert prose.region.y == thinking.region.bottom  # thinking: no gap
        assert tool.region.y == prose.region.bottom + 1
        title = tool.query_one("CollapsibleTitle")
        muted = thinking.query_one("CollapsibleTitle").styles.color
        assert title.styles.color == muted


@pytest.mark.asyncio
async def test_later_step_with_text_is_set_apart_from_tool_steps(tmp_path):
    """In a grouped turn, steps that only think and call tools are compact; a
    step that says something gets a blank line above it."""
    app = GptmeApp(make_manager(tmp_path, _turn_history()), workspace=tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await app.wait_rendered()
        await pilot.pause()
        steps = list(app.query(AssistantMessage))
        results = list(app.query(SystemMessage))
        assert steps[1].content_region.y == results[0].region.bottom  # compact
        # says something: a blank line inside the block (the bar continues)
        assert steps[2].region.y == results[1].region.bottom
        assert steps[2].content_region.y == results[1].region.bottom + 1


@pytest.mark.asyncio
async def test_chat_redrawn_after_a_command_keeps_the_agent_name(tmp_path, monkeypatch):
    """Commands (e.g. /compact) run off the UI thread; the chat re-rendered
    after one must see the session's config (the agent's name), not a
    default one from the worker thread's empty context."""
    from gptme.config import ChatConfig, Config
    from gptme.config.core import _config_var
    from gptme.config.models import AgentConfig

    monkeypatch.setattr(
        ChatConfig, "agent_config", property(lambda self: AgentConfig(name="Bob"))
    )
    token = _config_var.set(Config(chat=ChatConfig()))
    try:
        history = [
            Message("user", "hi"),
            Message("assistant", "hello"),
            Message("user", "again"),
            Message("assistant", "back"),
        ]
        app = GptmeApp(make_manager(tmp_path, history), workspace=tmp_path)
        async with app.run_test() as pilot:
            await app.wait_rendered()
            await pilot.pause()
            before = [
                str(m.query_one(".role").render()) for m in app.query(AssistantMessage)
            ]
            assert before == ["Bob", "Bob"], before
            inp = app.query_one("#input", ChatInput)
            inp.text = "/undo"  # changes the log: the chat is re-rendered
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause(0.02)
                if not app.generating:
                    break
            await app.wait_rendered()
            await pilot.pause()
            labels = [
                str(m.query_one(".role").render()) for m in app.query(AssistantMessage)
            ]
            assert labels and all(label == "Bob" for label in labels), labels
    finally:
        _config_var.reset(token)


@pytest.mark.asyncio
async def test_stream_marks_text_like_the_finished_message(tmp_path):
    """With thinking shown or hidden, the stream gets the same has-text
    spacing the finished message will, so replacing it moves nothing."""
    for show_thinking in (False, True):
        app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
        app.show_thinking = show_thinking
        async with app.run_test() as pilot:
            app._begin_stream()
            app._on_stream_token("<think>hm</think>The answer")
            app._flush_stream()
            await app.wait_rendered()
            await pilot.pause()
            assert app._stream_widget is not None
            assert app._stream_widget.has_class("has-text"), show_thinking
