"""Real Pandoc conversion keeps code literal while dropping presentation noise."""

import shutil
import subprocess
from html import escape

import pytest

pytest.importorskip("playwright")

from gptme.tools._browser_playwright import html_to_markdown

pytestmark = pytest.mark.skipif(
    not shutil.which("pandoc"), reason="Pandoc not installed"
)


@pytest.mark.parametrize("language", ["", ' class="language-python"'])
@pytest.mark.parametrize(
    "code",
    [
        'print("ordinary control")\n',
        "<div>literal example</div>\n",
        '::: warning\n{#literal-id}\n{.literal-class}\n{style="literal"}\n::: end\n',
        "before\n\n\nafter\n",
        "![literal](data:image/png;base64,YQ==)\n",
    ],
)
def test_block_code_matches_unmodified_pandoc(code: str, language: str) -> None:
    html = f"<pre><code{language}>" + escape(code) + "</code></pre>"
    raw = subprocess.run(
        ["pandoc", "-f", "html", "-t", "markdown"],
        input=html,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert html_to_markdown(html) == raw


def test_inline_code_keeps_literal_markup_and_attributes() -> None:
    html = "<p>Use <code>&lt;div&gt;{.example} :::</code> here.</p>"
    assert html_to_markdown(html) == "Use `<div>{.example} :::` here.\n"


def test_presentation_wrappers_attributes_and_embedded_images_removed() -> None:
    html = (
        '<div class="noise" id="wrapper"><h2 id="heading">Title</h2>'
        '<p>Ordinary <span class="noise">prose</span> '
        '<a href="https://example.test" target="_blank" class="noise">link</a></p>'
        '<img src="data:image/png;base64,YQ==" alt="embedded">'
        '<img src="https://example.test/image.png" alt="remote"></div>'
    )
    result = html_to_markdown(html)
    assert "Title" in result and "Ordinary prose" in result
    assert "[link](https://example.test)" in result
    assert "![remote](https://example.test/image.png)" in result
    assert "data:image" not in result
    assert ":::" not in result
    assert "{#" not in result and "{." not in result
    assert "target=" not in result and "<div" not in result


@pytest.mark.parametrize("block", [False, True])
def test_code_metadata_removed_without_editing_literal_text(block: bool) -> None:
    code = "<div>{.literal} :::</div>"
    attributes = ' class="language-python"' if block else ' class="noise"'
    attributed = (
        '<code id="example" style="color:red"'
        + attributes
        + ">"
        + escape(code)
        + "</code>"
    )
    html = "<pre>" + attributed + "</pre>" if block else "<p>" + attributed + "</p>"
    plain = (
        '<pre><code class="language-python">' + escape(code) + "</code></pre>"
        if block
        else "<p><code>" + escape(code) + "</code></p>"
    )
    assert html_to_markdown(html) == html_to_markdown(plain)
