"""Check the installation guidance published by the Sphinx docs."""

from io import StringIO
from pathlib import Path

from lxml import html
from sphinx.application import Sphinx

DOCS = Path(__file__).resolve().parents[1] / "docs"
RELEASES = "https://github.com/gptme/gptme/releases/latest"


def test_windows_desktop_installation(tmp_path: Path) -> None:
    warnings = StringIO()
    output = tmp_path / "html"
    app = Sphinx(
        DOCS,
        DOCS,
        output,
        tmp_path / "doctrees",
        "html",
        confoverrides={
            "root_doc": "getting-started",
            "include_patterns": [
                "getting-started.rst",
                "app.rst",
                "system-dependencies.rst",
            ],
            # The focused build omits the rest of the docs and the remote inventory.
            "intersphinx_mapping": {},
            "nitpicky": False,
            "suppress_warnings": ["ref.doc", "ref.ref", "toc.not_included"],
        },
        status=StringIO(),
        warning=warnings,
        warningiserror=True,
    )
    app.build()
    assert app.statuscode == 0, warnings.getvalue()

    page = html.fromstring(
        (output / "getting-started.html").read_text(encoding="utf-8")
    )
    installation = page.get_element_by_id("installation")
    text = installation.text_content()
    assert "Windows is not directly supported" not in text

    release_link = installation.find(f".//a[@href='{RELEASES}']")
    assert release_link is not None
    installer_paragraph = release_link.getparent()
    assert installer_paragraph is not None
    installer_guidance = installer_paragraph.text_content()
    assert "Windows" in installer_guidance
    assert "desktop app" in installer_guidance
    assert "-setup.exe" in installer_guidance
    assert "WSL" in text and "Docker" in text
