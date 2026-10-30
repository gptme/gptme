"""Screenshot destination preparation and one-shot page ownership."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

pytest.importorskip("playwright")

from gptme.tools import _browser_playwright as browser_tool


@pytest.mark.parametrize("as_path", [False, True])
@pytest.mark.parametrize(
    "shape", ["bare.png", "./bare.png", "nested/shot.png", "absolute", "~/shot.png"]
)
def test_screenshot_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_path: bool, shape: str
) -> None:
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    name = str(tmp_path / "absolute" / "shot.png") if shape == "absolute" else shape
    destination = Path(name) if as_path else name
    expected = Path(name).expanduser()
    managed = Mock()
    managed.page.url = "https://example.com/"

    def screenshot(*, path: Any) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")

    managed.page.screenshot.side_effect = screenshot
    monkeypatch.setattr(browser_tool, "_create_page", lambda browser: managed)
    result = browser_tool._take_screenshot(Mock(), "https://example.com/", destination)
    assert result == expected
    assert result.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert not (tmp_path / "~").exists()
    managed.page.goto.assert_called_once_with("https://example.com/")
    managed.close.assert_called_once_with()


@pytest.mark.parametrize("failure", [None, "navigation", "screenshot", "redirect"])
def test_screenshot_closes_managed_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    managed = Mock()
    managed.page.url = "https://example.com/"
    monkeypatch.setattr(browser_tool, "_create_page", lambda browser: managed)
    if failure == "navigation":
        managed.page.goto.side_effect = RuntimeError("navigation failed")
    elif failure == "screenshot":
        managed.page.screenshot.side_effect = RuntimeError("screenshot failed")
    elif failure == "redirect":
        monkeypatch.setattr(
            browser_tool,
            "_validate_page_url",
            Mock(side_effect=RuntimeError("redirect denied")),
        )
    destination = tmp_path / "shot.png"
    if failure:
        with pytest.raises(RuntimeError):
            browser_tool._take_screenshot(Mock(), "https://example.com/", destination)
    else:
        assert (
            browser_tool._take_screenshot(Mock(), "https://example.com/", destination)
            == destination
        )
    managed.close.assert_called_once_with()
    if failure in ("navigation", "redirect"):
        managed.page.screenshot.assert_not_called()


def test_screenshot_automatic_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    managed = Mock()
    managed.page.url = "https://example.com/"
    monkeypatch.setattr(browser_tool, "_create_page", lambda browser: managed)
    result = browser_tool._take_screenshot(Mock(), "https://example.com/")
    try:
        assert result.is_absolute() and result.suffix == ".png" and result.is_file()
        managed.page.screenshot.assert_called_once()
        assert Path(managed.page.screenshot.call_args.kwargs["path"]) == result
        managed.close.assert_called_once_with()
    finally:
        result.unlink()


def test_screenshot_validates_entry_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = Mock()
    monkeypatch.setattr(browser_tool, "_execute_with_retry", dispatch)
    monkeypatch.setattr(
        browser_tool,
        "_validate_entry_url",
        Mock(side_effect=ValueError("entry denied")),
    )
    with pytest.raises(ValueError, match="entry denied"):
        browser_tool.screenshot_url("https://denied.invalid/", "bare.png")
    dispatch.assert_not_called()
