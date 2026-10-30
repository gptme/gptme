"""Prompt path mentions are prose; only deliberate references attach resources."""

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


def test_explicit_reference_inside_code_or_xml_is_not_attached(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.txt").write_text("not attached")
    msg = Message("user", "```text\n@data.txt\n```\n<result>@data.txt</result>")
    assert context._include_paths(msg) is msg


@pytest.mark.parametrize("key", ["control-v", "bracketed-paste"])
@pytest.mark.parametrize("native_image", [False, True])
def test_clipboard_handler_emits_working_attachment(
    tmp_path, monkeypatch, key, native_image
):
    from types import SimpleNamespace

    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding.key_processor import KeyPressEvent
    from prompt_toolkit.keys import Keys

    from gptme.util import clipboard, prompt

    picture = tmp_path / "clipboard picture.png"
    Image.new("RGB", (2, 2), "blue").save(picture)
    monkeypatch.setattr(prompt, "_prompt_session", None)
    monkeypatch.setattr(prompt, "get_pt_history_file", lambda: tmp_path / "history")
    monkeypatch.setattr(
        prompt, "PromptSession", lambda **kwargs: SimpleNamespace(**kwargs)
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
    assert buffer.text == f"`@{picture}`"
    result = context._include_paths(Message("user", buffer.text))
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
