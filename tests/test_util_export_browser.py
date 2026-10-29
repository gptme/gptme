"""Browser regressions for the shared standalone/server HTML export template."""

from collections.abc import Iterator
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from gptme.logmanager import Log
from gptme.message import Message
from gptme.util.export import _embed_cdn_resources, export_chat_to_html
from gptme.util.uri import URI

if TYPE_CHECKING:
    from playwright.sync_api import Page

playwright = pytest.importorskip("playwright.sync_api")

# Real template dependencies are fetched (or integrity-checked from the CDN cache).
pytestmark = pytest.mark.slow
TEMPLATES = Path(__file__).parents[1] / "gptme/util/export_templates"


@pytest.fixture
def page() -> Iterator["Page"]:
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            context = browser.new_context(offline=True)
            yield context.new_page()
        finally:
            browser.close()


def test_standalone_attachments_are_explicitly_omitted(
    page: "Page", tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    private = source / "private.txt"
    private.write_text("PRIVATE_ATTACHMENT_BYTES")
    image = source / "picture.png"
    image.write_bytes(b"PRIVATE_IMAGE_BYTES")
    large = source / "large.bin"
    with large.open("wb") as stream:
        stream.truncate(128 * 1024 * 1024)
    files: list[Path | URI] = [
        private,
        image,
        large,
        source / "missing.pdf",
        source / '<b>label & "quoted".txt',
        URI("https://example.invalid/remote.png"),
        URI("file:///unavailable/local.txt"),
    ]
    output = tmp_path / "export.html"
    log = Log([Message("user", "Visible **text** and `code`", files=files)])
    # Guard actual attachment reads, while allowing template/CDN-cache reads.
    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        assert not path.is_relative_to(source), f"Attachment read: {path}"
        return original_open(path, *args, **kwargs)

    with patch.object(Path, "open", guarded_open):
        export_chat_to_html("synthetic", log, output)
    assert "PRIVATE_ATTACHMENT_BYTES" not in output.read_text()
    assert "PRIVATE_IMAGE_BYTES" not in output.read_text()
    # Neither the original log nor any attachment is needed to view the export.
    for path in source.iterdir():
        path.unlink()
    source.rmdir()
    errors: list[str] = []
    requests: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.goto(output.as_uri())
    message = page.locator(".chat-msg:visible")
    message.wait_for()
    assert message.locator("strong").inner_text() == "text"
    assert message.locator("code").inner_text() == "code"
    omitted = message.locator(".attachment-omitted")
    assert omitted.count() == len(files)
    labels = [
        "private.txt",
        "picture.png",
        "large.bin",
        "missing.pdf",
        '<b>label & "quoted".txt',
        "remote.png",
        "local.txt",
    ]
    for label, text in zip(labels, omitted.all_text_contents(), strict=True):
        assert label in text
        assert "Attachment omitted" in text
    assert message.locator("a, img, b").count() == 0
    assert not errors
    assert not any("/files/" in url or "example.invalid" in url for url in requests)


def test_server_template_keeps_attachment_links_and_images(page: "Page") -> None:
    template = _embed_cdn_resources((TEMPLATES / "index.html").read_text())
    template = template.replace(
        '<script type="module" src="/static/main.js"></script>',
        f'<script type="module">{(TEMPLATES / "main.js").read_text()}</script>',
    )
    page.route(
        "http://export.test/",
        lambda route: route.fulfill(body=template, content_type="text/html"),
    )
    page.route("**/api/config", lambda route: route.fulfill(json={}))
    page.route("**/api/v2/commands", lambda route: route.fulfill(json={"commands": []}))
    page.route("**/api/conversations?*", lambda route: route.fulfill(json=[]))
    # Route fulfillment is local, even with an offline browser context.
    page.goto("http://export.test/")
    page.wait_for_function("document.querySelector('#app').__vue__")
    page.evaluate("""() => {
        const app = document.querySelector('#app').__vue__;
        app.selectedConversation = 'synthetic';
        app.branches = {main: []};
        app.chatLog = [{role: 'user', content: 'Server control',
            timestamp: '2026-01-01T00:00:00Z', files: ['notes.txt', 'photo.png']}];
    }""")
    message = page.locator(".chat-msg:visible")
    message.wait_for()
    assert message.locator(".attachment-omitted").count() == 0
    assert message.locator("a").count() == 2
    assert (
        message.locator("a").nth(0).get_attribute("href")
        == "/api/conversations/synthetic/files/notes.txt"
    )
    assert (
        message.locator("img").get_attribute("src")
        == "/api/conversations/synthetic/files/photo.png"
    )
