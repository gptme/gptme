"""Prompt path mentions are prose; only deliberate references attach resources."""

import errno
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gptme.message import Message
from gptme.util import context


@pytest.fixture(autouse=True)
def attachment_policy(monkeypatch):
    monkeypatch.delenv("GPTME_DISABLE_PATH_INCLUDE", raising=False)
    monkeypatch.delenv("GPTME_AUTO_INCLUDE_PATHS", raising=False)
    monkeypatch.setattr(context, "use_fresh_context", lambda: False)


@pytest.mark.parametrize(
    "form", ["relative", "absolute", "home", "spaces", "fenced", "quoted", "repeated"]
)
def test_plain_mentions_never_inspect_resources(tmp_path, monkeypatch, form):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "large.py"
    source.write_text("print('source')\n" * 100_000)
    (tmp_path / "my notes.txt").write_text("private notes")
    directory = tmp_path / "src"
    directory.mkdir()
    (directory / "child.py").write_text("private child")
    prompts = {
        "relative": "Inspect large.py and ./src/ later.\n",
        "absolute": f"  Diagnostic at {source} in {directory}/\n",
        "home": "See ~/large.py and ~/src/",
        "spaces": "The file `my notes.txt` failed",
        "fenced": f"```text\n{source}\n{directory}\n```\n",
        "quoted": f'Error: "{source}" under `{directory}`',
        "repeated": f"{source} {source} ./large.py large.py {directory} {directory}",
    }
    original = Message("user", prompts[form])
    with (
        patch.object(
            Path, "iterdir", side_effect=AssertionError("directory scan")
        ) as scan,
        patch.object(
            context, "_resource_to_codeblock", side_effect=AssertionError("read")
        ) as read,
        patch.object(
            context, "_parse_prompt_files", side_effect=AssertionError("attachment")
        ) as attach,
    ):
        result = context._include_paths(original, tmp_path)
    assert result is original
    assert result.content == prompts[form]
    scan.assert_not_called()
    read.assert_not_called()
    attach.assert_not_called()


@pytest.mark.parametrize(
    "reference",
    [
        "@data.txt",
        "@./data.txt",
        "@~/data.txt",
        "`@data.txt`",
        "@Makefile",
        "`@my notes.txt`",
    ],
)
def test_explicit_files_attach_without_cwd_scan(tmp_path, monkeypatch, reference):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("data.txt", "Makefile", "my notes.txt"):
        (tmp_path / name).write_text("deliberate-content-marker")
    with patch.object(Path, "iterdir", side_effect=AssertionError("cwd scan")):
        result = context._include_paths(
            Message("user", f"data.txt then {reference} and {reference}")
        )
    assert result.content.count("deliberate-content-marker") == 1


def test_explicit_directory_still_attaches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "child.py").write_text("contents not needed in listing")
    result = context._include_paths(Message("user", "Inspect @src"))
    assert "child.py" in result.content
    assert "contents not needed" not in result.content


@pytest.mark.parametrize("fresh", [False, True])
def test_explicit_image_and_attached_metadata_preserved(tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(context, "use_fresh_context", lambda: fresh)
    picture = tmp_path / "my image.png"
    Image.new("RGB", (2, 2), "red").save(picture)
    prior = tmp_path / "previous.png"
    original = Message("user", f"Look at `@{picture}`", files=[prior])
    result = context._include_paths(original)
    assert result.files == [prior, picture]
    plain = Message("user", str(picture), files=[prior])
    assert context._include_paths(plain) is plain


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_legacy_opt_in(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", value)
    (tmp_path / "source.py").write_text("legacy-content-marker")
    result = context._include_paths(Message("user", "See source.py"))
    assert "legacy-content-marker" in result.content


@pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
def test_false_opt_in_keeps_plain_paths(tmp_path, monkeypatch, value):
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", value)
    source = tmp_path / "source.py"
    source.write_text("not attached")
    original = Message("user", str(source))
    assert context._include_paths(original) is original


def test_disable_overrides_explicit_and_legacy_without_removing_files(monkeypatch):
    monkeypatch.setenv("GPTME_DISABLE_PATH_INCLUDE", "1")
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1")
    msg = Message("user", "@file.txt https://example.com", files=[Path("image.png")])
    with patch.object(
        context, "_find_potential_paths", side_effect=AssertionError("scan")
    ):
        assert context._include_paths(msg) is msg


def test_urls_keep_separate_consent_without_directory_scan(monkeypatch):
    url = "https://example.com/page"
    monkeypatch.setattr(context, "_is_interactive_mode", lambda: True)
    with (
        patch.object(Path, "iterdir", side_effect=AssertionError("cwd scan")),
        patch.object(context, "_confirm_urls", return_value=[]) as confirm,
        patch.object(context, "_resource_to_codeblock", return_value="fetched") as read,
    ):
        msg = Message("user", f"Read ./ordinary.txt and {url}")
        assert context.extract_urls(msg.content) == [url]
        assert context._include_paths(msg) is msg
        confirm.assert_called_once_with([url])
        read.assert_not_called()
        result = context._include_paths(msg, pre_confirmed_urls=[url])
        assert result.content.endswith("fetched")
        read.assert_called_once_with(url, confirmed_urls=[url])


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("scheme", ["httpS", "hTtP"])
@pytest.mark.parametrize("form", ["bare", "backticks", "quoted-prose"])
@pytest.mark.parametrize("accepted", [False, True])
def test_mixed_case_url_preserves_interactive_consent(
    monkeypatch, legacy, scheme, form, accepted
):
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1" if legacy else "0")
    monkeypatch.setattr(context, "_is_interactive_mode", lambda: True)
    url = f"{scheme}://example.com/CaseSensitive?Key=Value"
    reference = {
        "bare": url,
        "backticks": f"`{url}`",
        "quoted-prose": f"`see {url}`",
    }[form]
    msg = Message("user", f"Read {reference}")
    confirmed = [url] if accepted else []
    with (
        patch.object(context, "_confirm_urls", return_value=confirmed) as confirm,
        patch.object(context, "_resource_to_codeblock", return_value="fetched") as read,
    ):
        assert context.extract_urls(msg.content) == [url]
        result = context._include_paths(msg)
        confirm.assert_called_once_with([url])
        if accepted:
            assert result.content.endswith("fetched")
            read.assert_called_once_with(url, confirmed_urls=[url])
        else:
            assert result is msg
            read.assert_not_called()


def test_explicit_reference_inside_code_or_xml_is_not_attached(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.txt").write_text("not attached")
    msg = Message("user", "```text\n@data.txt\n```\n<result>@data.txt</result>")
    assert context._include_paths(msg) is msg


@pytest.mark.parametrize("key", ["control-v", "bracketed-paste"])
@pytest.mark.parametrize("native_image", [False, True])
@pytest.mark.parametrize("unsafe_name", ["", "backtick", "lf", "cr"])
def test_clipboard_handler_emits_working_attachment(
    tmp_path, monkeypatch, key, native_image, unsafe_name
):
    from types import SimpleNamespace

    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent
    from prompt_toolkit.keys import Keys

    from gptme.util import clipboard, prompt

    monkeypatch.chdir(tmp_path)
    (tmp_path / "secret.txt").write_text("synthetic-wrong-file-marker")
    names = {
        "": "clipboard picture.png",
        "backtick": "photo `@secret.txt` album.png",
        "lf": "photo\n@secret.txt\nalbum.png",
        "cr": "photo\r@secret.txt\ralbum.png",
    }
    picture = tmp_path / names[unsafe_name]
    Image.new("RGB", (2, 2), "blue").save(picture)
    if unsafe_name == "cr":
        # Isolate CR identity: an LF alias would hide pre-detection normalization.
        assert not Path(str(picture).replace("\r", "\n")).exists()
    monkeypatch.setattr(prompt, "_prompt_session", None)
    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    monkeypatch.setattr(
        prompt,
        "PromptSession",
        lambda **kwargs: SimpleNamespace(
            app=SimpleNamespace(on_reset=MagicMock()), **kwargs
        ),
    )
    monkeypatch.setattr(
        clipboard, "paste_image", lambda: picture if native_image else None
    )
    monkeypatch.setattr(clipboard, "paste_text", lambda: str(picture))
    session = prompt.get_prompt_session()
    buffer = Buffer()
    event = MagicMock(spec=KeyPressEvent)
    event.current_buffer = buffer
    event.data = str(picture)
    keys = (Keys.ControlV if key == "control-v" else Keys.BracketedPaste,)
    assert session.key_bindings is not None
    bindings = session.key_bindings.get_bindings_for_keys(keys)
    assert len(bindings) == 1
    bindings[0].handler(event)
    result = context._include_paths(Message("user", buffer.text))
    assert "synthetic-wrong-file-marker" not in result.content
    if unsafe_name:
        # Reject without echoing the filename or falling back to raw paste.
        assert "@secret.txt" not in buffer.text
        assert str(picture) not in buffer.text
        assert not result.files
    else:
        assert buffer.text == f"`@{picture}`"
        assert result.files == [picture]


def test_programmatic_text_attachment_survives_plain_default(tmp_path):
    source = tmp_path / "programmatic.txt"
    source.write_text("programmatic-content-marker")
    original = Message("user", "Review this", files=[source])
    included = context._include_paths(original)
    assert included is original
    embedded = context.embed_attached_file_content(included)
    assert "programmatic-content-marker" in embedded.content


def test_quoted_url_cannot_sneak_in_an_ordinary_local_path(tmp_path, monkeypatch):
    source = tmp_path / "secret.txt"
    source.write_text("must-not-attach")
    url = "https://example.com/page"
    msg = Message("user", f"Read `{url} {source}`")
    with patch.object(
        context, "_resource_to_codeblock", return_value="url-only"
    ) as read:
        result = context._include_paths(msg, pre_confirmed_urls=[url])
    read.assert_called_once_with(url, confirmed_urls=[url])
    assert "must-not-attach" not in result.content


@pytest.mark.parametrize("legacy", [False, True])
def test_missing_spaced_attachment_cannot_expand_individual_words(
    tmp_path, monkeypatch, legacy
):
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1" if legacy else "0")
    source = tmp_path / "secret.txt"
    source.write_text("unrequested-content-marker")
    url = "https://example.com/page"
    msg = Message("user", f"Check `@missing {source} {url}`")
    with patch.object(
        context, "_resource_to_codeblock", side_effect=AssertionError("read")
    ) as read:
        assert context._include_paths(msg, pre_confirmed_urls=[]) is msg
    read.assert_not_called()


def _apply_first_completion(text: str) -> str:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from gptme.util.prompt import GptmeCompleter, clear_path_cache

    clear_path_cache()
    completions = list(
        GptmeCompleter().get_completions(Document(text), CompleteEvent())
    )
    assert completions, f"expected path completions for {text!r}"
    completion = completions[0]
    return text[: len(text) + completion.start_position] + completion.text


def test_cli_completes_explicit_attachment_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("readme")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils.py").write_text("utils")
    monkeypatch.setattr(
        "gptme.util.prompt.llm_suggest",
        lambda text: (_ for _ in ()).throw(AssertionError("llm")),
    )
    assert _apply_first_completion("review @REA") == "review @README.md"
    assert _apply_first_completion("review REA") == "review README.md"
    assert _apply_first_completion("see @src/u") == "see @src/utils.py"
    (tmp_path / "photo album.txt").write_text("spaced-content-marker")
    completed = _apply_first_completion("see @pho")
    assert completed == "see `@photo album.txt`"
    result = context._include_paths(Message("user", completed))
    assert "spaced-content-marker" in result.content
    secret = tmp_path / "secret.txt"
    secret.write_text("must-not-attach")
    (tmp_path / "photo `@secret.txt` album.txt").write_text("nested-tick-name")
    tick_completed = _apply_first_completion("see @pho")
    assert tick_completed == "see `@photo album.txt`"
    tick_result = context._include_paths(Message("user", tick_completed))
    assert "must-not-attach" not in tick_result.content
    assert "spaced-content-marker" in tick_result.content
    (tmp_path / "my dir").mkdir()
    (tmp_path / "my dir" / "child.md").write_text("child-content-marker")
    directory = _apply_first_completion("see @my")
    assert directory == "see `@my dir/"
    child = _apply_first_completion(directory + "ch")
    assert child == "see `@my dir/child.md`"
    child_result = context._include_paths(Message("user", child))
    assert "child-content-marker" in child_result.content


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_command_backticks_expand_embedded_path(tmp_path, monkeypatch, legacy):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1" if legacy else "0")
    (tmp_path / "main.py").write_text("legacy-source-marker")
    url = "https://example.com/page"
    msg = Message("user", f"Explain `./main.py --help {url}`")
    with patch.object(
        context, "_resource_to_codeblock", wraps=context._resource_to_codeblock
    ) as read:
        result = context._include_paths(msg, pre_confirmed_urls=[])
    if legacy:
        assert "legacy-source-marker" in result.content
        assert "./main.py" in result.content
    else:
        assert result is msg
        assert "legacy-source-marker" not in result.content
    assert "[EXTERNAL CONTENT" not in result.content
    assert not any(
        call.args and str(call.args[0]).lower().startswith(("http://", "https://"))
        for call in read.call_args_list
    )


def test_overlong_explicit_path_does_not_drop_sibling_attachment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "good.txt").write_text("keep-this-marker")
    original_exists = Path.exists

    def exists_with_overlong(self):
        if len(str(self)) > 200:
            raise OSError(errno.ENAMETOOLONG, "name too long")
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", exists_with_overlong)
    long_name = "@./" + ("a" * 300) + ".txt"
    result = context._include_paths(Message("user", f"Read @good.txt {long_name}"))
    assert "keep-this-marker" in result.content


def test_explicit_command_backticks_do_not_expand_embedded_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1")
    (tmp_path / "main.py").write_text("legacy-source-marker")
    msg = Message("user", "Explain `@./main.py --help`")
    result = context._include_paths(msg)
    assert result is msg
    assert "legacy-source-marker" not in result.content


@pytest.mark.parametrize("legacy", [False, True])
def test_quoted_prose_url_respects_interactive_consent(monkeypatch, legacy):
    monkeypatch.setenv("GPTME_AUTO_INCLUDE_PATHS", "1" if legacy else "0")
    monkeypatch.setattr(context, "_is_interactive_mode", lambda: True)
    url = "https://example.com/page"
    msg = Message("user", f"Read `see {url}`")
    with (
        patch.object(context, "_confirm_urls", return_value=[]) as confirm,
        patch.object(
            context, "_resource_to_codeblock", side_effect=AssertionError("read")
        ) as read,
    ):
        assert context._include_paths(msg) is msg
    confirm.assert_called_once_with([url])
    read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("follow_up", [False, True])
async def test_cli_completed_directory_submit(
    tmp_path, monkeypatch, child: bool, follow_up: bool
) -> None:
    """Use the real prompt buffer and Enter binding, including child continuation."""
    from weakref import ref

    from prompt_toolkit import PromptSession
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Never
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.output import DummyOutput

    from gptme.util import prompt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prompt, "_prompt_session", None)
    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    monkeypatch.setattr(
        prompt, "llm_suggest", lambda text: pytest.fail("unexpected inference")
    )
    (tmp_path / "my dir").mkdir()
    (tmp_path / "my dir" / "child.md").write_text("synthetic-child-marker")
    (tmp_path / "secret.txt").write_text("wrong-file-marker")
    prompt.clear_path_cache()
    with patch(
        "gptme.util.prompt.PromptSession",
        side_effect=lambda **kw: PromptSession(
            input=DummyInput(), output=DummyOutput(), **kw
        ),
    ):
        session = prompt.get_prompt_session()
    buffer = session.default_buffer
    buffer.validate_while_typing = Never()
    buffer.complete_while_typing = Never()
    buffer.document = Document("see @my")
    completer = session.completer
    assert completer is not None
    completion = next(iter(completer.get_completions(buffer.document, CompleteEvent())))
    buffer.apply_completion(completion)
    assert buffer.text == "see `@my dir/"
    # prompt_toolkit can automatically request child suggestions after Tab.
    if follow_up:
        list(completer.get_completions(buffer.document, CompleteEvent()))
    if child:
        buffer.insert_text("ch")
        completion = next(
            iter(completer.get_completions(buffer.document, CompleteEvent()))
        )
        buffer.apply_completion(completion)
        assert buffer.text == "see `@my dir/child.md`"
    submitted: list[str] = []

    def accept(buf: Buffer) -> bool:
        submitted.append(buf.text)
        return True

    buffer.accept_handler = accept
    assert session.key_bindings is not None
    enter = session.key_bindings.get_bindings_for_keys((Keys.ControlM,))[-1]
    with set_app(session.app):
        event = KeyPressEvent(ref(session.app.key_processor), None, [], [], False)
        enter.handler(event)
    expected = "see `@my dir/child.md`" if child else "see `@my dir/`"
    assert submitted == [expected]
    result = context._include_paths(Message("user", submitted[0]))
    assert "child.md" in result.content
    assert ("synthetic-child-marker" in result.content) is child
    assert "wrong-file-marker" not in result.content

    # Once submitted, the same open text typed as prose is not auto-closed.
    buffer.document = Document("see `@my dir/")
    with set_app(session.app):
        enter.handler(event)
    assert submitted[-1] == "see `@my dir/"
    prompt.clear_path_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("follow_up", [False, True])
async def test_cli_aborted_directory_completion_does_not_authorize_next_prompt(
    tmp_path, monkeypatch, follow_up: bool
) -> None:
    """Native Tab/Ctrl-C, then paste without Tab in the reused PromptSession."""
    import asyncio
    from collections.abc import Callable

    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from gptme.util import prompt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prompt, "_prompt_session", None)
    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    monkeypatch.setattr(
        prompt, "llm_suggest", lambda text: pytest.fail("unexpected inference")
    )
    monkeypatch.setattr("gptme.util.clipboard.paste_image", lambda: None)
    (tmp_path / "my dir").mkdir()
    (tmp_path / "my dir" / "child.md").write_text("synthetic-child-marker")
    prompt.clear_path_cache()

    async def wait_until(predicate: Callable[[], bool]) -> None:
        async def poll() -> None:
            while not predicate():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(poll(), timeout=3)

    with create_pipe_input() as pipe:
        with patch(
            "gptme.util.prompt.PromptSession",
            side_effect=lambda **kw: PromptSession(
                input=pipe, output=DummyOutput(), **kw
            ),
        ):
            session = prompt.get_prompt_session()

        async def read_prompt() -> str | None:
            try:
                return await session.prompt_async()
            except KeyboardInterrupt:
                return None

        first = asyncio.create_task(read_prompt())
        await wait_until(lambda: session.app.is_running)
        pipe.send_text("see @my\t")
        await wait_until(lambda: session.default_buffer.text == "see `@my dir/")
        if follow_up:
            await asyncio.sleep(0.1)
        pipe.send_bytes(b"\x03")
        assert await asyncio.wait_for(first, timeout=3) is None

        second = asyncio.create_task(read_prompt())
        await wait_until(lambda: session.app.is_running)
        pipe.send_text("\x1b[200~see `@my dir/\x1b[201~")
        await wait_until(lambda: session.default_buffer.text == "see `@my dir/")
        if follow_up:
            await asyncio.sleep(0.1)
        pipe.send_text("\r")
        submitted = await asyncio.wait_for(second, timeout=3)
        assert submitted == "see `@my dir/"
        result = context._include_paths(Message("user", submitted))
        assert "child.md" not in result.content
    prompt.clear_path_cache()


@pytest.mark.parametrize("key", ["control-v", "bracketed-paste"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ordinary clipboard text", "ordinary clipboard text"),
        ("first\r\nsecond\rthird", "first\r\nsecond\rthird"),
        (
            "https://example.com/image.png",
            "View this image: https://example.com/image.png",
        ),
    ],
)
def test_clipboard_image_rejection_preserves_text_and_urls(
    tmp_path, monkeypatch, key: str, text: str, expected: str
) -> None:
    from types import SimpleNamespace

    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent
    from prompt_toolkit.keys import Keys

    from gptme.util import clipboard, prompt

    monkeypatch.setattr(prompt, "_prompt_session", None)
    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    monkeypatch.setattr(
        prompt,
        "PromptSession",
        lambda **kwargs: SimpleNamespace(
            app=SimpleNamespace(on_reset=MagicMock()), **kwargs
        ),
    )
    monkeypatch.setattr(clipboard, "paste_image", lambda: None)
    monkeypatch.setattr(clipboard, "paste_text", lambda: text)
    session = prompt.get_prompt_session()
    buffer = Buffer()
    event = MagicMock(spec=KeyPressEvent)
    event.current_buffer = buffer
    event.data = text
    keys = (Keys.ControlV if key == "control-v" else Keys.BracketedPaste,)
    assert session.key_bindings is not None
    session.key_bindings.get_bindings_for_keys(keys)[0].handler(event)
    if key == "bracketed-paste":
        expected = expected.replace("\r\n", "\n").replace("\r", "\n")
    assert buffer.text == expected


@pytest.mark.parametrize("first_name", ["first.txt", "first file.txt"])
def test_cli_completion_reuses_closing_backtick_and_preserves_suffix(
    tmp_path, monkeypatch, first_name: str
) -> None:
    """Editing a closed reference must not corrupt a later spaced attachment."""
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from gptme.util import prompt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        prompt, "llm_suggest", lambda text: pytest.fail("unexpected inference")
    )
    (tmp_path / first_name).write_text("synthetic-first-file-marker")
    (tmp_path / "second file.txt").write_text("synthetic-second-file-marker")
    prompt.clear_path_cache()
    try:
        prefix = "see `@fir"
        suffix = "` and `@second file.txt` keep trailing prose"
        buffer = Buffer(document=Document(prefix + suffix, cursor_position=len(prefix)))
        completion = next(
            iter(
                prompt.GptmeCompleter().get_completions(
                    buffer.document, CompleteEvent()
                )
            )
        )
        buffer.apply_completion(completion)
        assert buffer.text == f"see `@{first_name}{suffix}"
        assert buffer.document.text_after_cursor == suffix
        result = context._include_paths(Message("user", buffer.text))
        assert result.content.count("synthetic-first-file-marker") == 1
        assert result.content.count("synthetic-second-file-marker") == 1
        assert "keep trailing prose" in result.content
    finally:
        prompt.clear_path_cache()
