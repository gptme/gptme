"""The TUI answers ``elicit`` requests in a docked dialog, not via stdin."""

import asyncio
import json
import threading

import pytest

pytest.importorskip("textual")

from gptme.hooks import HookType, get_hooks
from gptme.hooks.elicitation import (
    ElicitationRequest,
    ElicitationResponse,
    FormField,
    elicit,
)
from gptme.logmanager import LogManager
from gptme.tui.app import ElicitScreen, GptmeApp


def _app(tmp_path) -> GptmeApp:
    return GptmeApp(
        LogManager([], logdir=tmp_path / "chat", lock=False), workspace=tmp_path
    )


def _worker(app: GptmeApp, fn) -> threading.Thread:
    """A thread in the app's context, like its generation worker (hooks live there)."""
    return threading.Thread(target=lambda: app._chat_ctx.copy().run(fn))


async def _ask(app, pilot, request, keys: list[str]) -> ElicitationResponse:
    """Run the hook on a worker thread (as tools do), answer with ``keys``."""
    box: dict[str, ElicitationResponse] = {}
    thread = _worker(app, lambda: box.update(r=elicit(request)))
    thread.start()
    for _ in range(200):
        await pilot.pause()
        if isinstance(app.screen, ElicitScreen):
            break
        await asyncio.sleep(0.01)
    assert isinstance(app.screen, ElicitScreen)
    # dock above the prompt like the confirmation dialogs
    dialog = app.screen.query_one("#confirm-dialog")
    assert dialog.region.bottom == app.screen_stack[0].query_one("#input-row").region.y
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    for _ in range(200):
        if not thread.is_alive():
            break
        await pilot.pause()
        await asyncio.sleep(0.01)
    thread.join(timeout=5)
    assert not thread.is_alive()
    return box["r"]


@pytest.mark.asyncio
async def test_hook_registered_and_removed(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "tui_elicit" in [h.name for h in get_hooks(HookType.ELICIT)]
    assert "tui_elicit" not in [h.name for h in get_hooks(HookType.ELICIT)]


@pytest.mark.asyncio
async def test_text(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        r = await _ask(
            app, pilot, ElicitationRequest("text", "Name?"), ["a", "b", "enter"]
        )
        assert r.value == "ab" and not r.sensitive and not r.cancelled


@pytest.mark.asyncio
async def test_text_default_on_empty(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        r = await _ask(
            app, pilot, ElicitationRequest("text", "Name?", default="bob"), ["enter"]
        )
        assert r.value == "bob"


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", ["x", " pass ", "   "])
async def test_secret_is_masked_and_sensitive(tmp_path, secret):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        req = ElicitationRequest("secret", "Key?")
        box: dict[str, ElicitationResponse] = {}
        t = _worker(app, lambda: box.update(r=elicit(req)))
        t.start()
        for _ in range(200):
            await pilot.pause()
            if isinstance(app.screen, ElicitScreen):
                break
            await asyncio.sleep(0.01)
        assert app.screen.query_one("#elicit-input").password is True  # type: ignore[attr-defined]
        for key in [*secret, "enter"]:
            await pilot.press(key)
        t.join(timeout=5)
        assert box["r"].value == secret and box["r"].sensitive


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "expected"),
    [(["2"], "b"), (["down", "down", "enter"], "c"), (["enter"], "a")],
)
async def test_choice(tmp_path, keys, expected):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        req = ElicitationRequest("choice", "Pick", options=["a", "b", "c"])
        assert (await _ask(app, pilot, req, keys)).value == expected


@pytest.mark.asyncio
async def test_multi_choice(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        req = ElicitationRequest("multi_choice", "Pick", options=["a", "b", "c"])
        r = await _ask(app, pilot, req, ["space", "down", "down", "space", "enter"])
        assert r.values == ["a", "c"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("default", "key", "expected"),
    [
        (None, "y", "yes"),
        (None, "n", "no"),
        (None, "enter", "yes"),
        (False, "enter", "no"),
        (" No ", "enter", "no"),
        ("1", "enter", "yes"),
    ],
)
async def test_confirmation(tmp_path, default, key, expected):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        req = ElicitationRequest("confirmation", "Sure?", default=default)
        assert (await _ask(app, pilot, req, [key])).value == expected


@pytest.mark.asyncio
async def test_escape_cancels(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        r = await _ask(app, pilot, ElicitationRequest("text", "Name?"), ["escape"])
        assert r.cancelled


@pytest.mark.asyncio
async def test_interrupt_cancels_pending_dialog(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        box: dict[str, ElicitationResponse] = {}
        req = ElicitationRequest("text", "Name?")
        t = _worker(app, lambda: box.update(r=elicit(req)))
        t.start()
        for _ in range(200):
            await pilot.pause()
            if isinstance(app.screen, ElicitScreen):
                break
            await asyncio.sleep(0.01)
        assert app._dismiss_dialog()
        for _ in range(100):
            await pilot.pause()
            if not t.is_alive():
                break
            await asyncio.sleep(0.01)
        t.join(timeout=5)
        assert box["r"].cancelled


@pytest.mark.asyncio
async def test_form_runs_fields_through_dialogs(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        req = ElicitationRequest(
            "form",
            "Setup",
            fields=[
                FormField("name", "Name?"),
                FormField("age", "Age?", type="number"),
                FormField("ok", "Ok?", type="boolean"),
            ],
        )
        box: dict[str, ElicitationResponse] = {}
        t = _worker(app, lambda: box.update(r=elicit(req)))
        t.start()
        for answer in (["b", "o", "b", "enter"], ["4", "2", "enter"], ["y"]):
            for _ in range(200):
                await pilot.pause()
                if isinstance(app.screen, ElicitScreen):
                    break
                await asyncio.sleep(0.01)
            assert isinstance(app.screen, ElicitScreen)
            for key in answer:
                await pilot.press(key)
            for _ in range(100):  # wait for the dialog to close
                await pilot.pause()
                if not isinstance(app.screen, ElicitScreen):
                    break
        t.join(timeout=5)
        assert json.loads(box["r"].value or "") == {
            "name": "bob",
            "age": 42,
            "ok": True,
        }


@pytest.mark.parametrize("fields", [None, []])
def test_empty_form_uses_supplied_backend(fields, monkeypatch):
    from gptme.hooks.elicitation import _cli_form

    def no_stdin(*args):
        pytest.fail("empty form must not read stdin with a supplied backend")

    monkeypatch.setattr("builtins.input", no_stdin)
    requests = []

    def ask(request):
        requests.append(request)
        return ElicitationResponse.text("bob")

    request = ElicitationRequest(
        "form", "Name?", fields=fields, default="alice", description="Your name"
    )
    assert _cli_form(request, ask=ask).value == "bob"
    assert len(requests) == 1
    assert requests[0].type == "text"
    assert requests[0].prompt == request.prompt
    assert requests[0].default == request.default
    assert requests[0].description == request.description


@pytest.mark.parametrize(
    ("kind", "default", "options"),
    [
        ("confirmation", 1, None),
        ("confirmation", 0, None),
        ("confirmation", "maybe", None),
        ("choice", "missing", ["a", "b"]),
        ("choice", False, ["a", "b"]),
    ],
)
@pytest.mark.parametrize("in_form", [False, True])
def test_invalid_defaults_cancel_before_opening_dialog(
    tmp_path, monkeypatch, kind, default, options, in_form
):
    app = _app(tmp_path)

    def no_dialog():
        pytest.fail("invalid default must cancel before opening a dialog")

    monkeypatch.setattr(app, "_restore_terminal", no_dialog)
    monkeypatch.setattr(app, "_elicit_notice", lambda message: None)
    request = ElicitationRequest(kind, "Pick", default=default, options=options)
    if in_form:
        request = ElicitationRequest(
            "form",
            "Setup",
            fields=[
                FormField(
                    "field",
                    "Pick",
                    type="boolean" if kind == "confirmation" else "choice",
                    default=default,
                    options=options,
                )
            ],
        )
    assert app._tui_elicit_hook(request).cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [None, []])
async def test_empty_form_opens_text_dialog(tmp_path, monkeypatch, fields):
    def no_stdin(*args):
        pytest.fail("empty TUI form must not read stdin")

    monkeypatch.setattr("builtins.input", no_stdin)
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        request = ElicitationRequest("form", "Name?", fields=fields)
        response = await _ask(app, pilot, request, ["b", "o", "b", "enter"])
        assert response.value == "bob" and not response.cancelled
