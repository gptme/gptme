"""Bound oversized browser results and spill them to tool-outputs (#4663)."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import patch

from gptme.message import Message
from gptme.tools.base import ToolSpec
from gptme.tools.browser import (
    _DEFAULT_BROWSER_MAX_OUTPUT_CHARS,
    BROWSER_MAX_OUTPUT_CHARS_ENV,
    _bound_browser_result,
    _browser_max_output_chars,
    read_url,
    tool,
)


def _as_messages(result: Generator[Message, None, None] | Message) -> list[Message]:
    if isinstance(result, Message):
        return [result]
    return list(result)


def test_bound_browser_result_passes_short_results_through(monkeypatch):
    monkeypatch.setenv(BROWSER_MAX_OUTPUT_CHARS_ENV, "100")
    assert _bound_browser_result("x" * 100, "read_url") == "x" * 100


def test_bound_browser_result_keeps_head_and_tail_and_saves_full_text(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(BROWSER_MAX_OUTPUT_CHARS_ENV, "100")
    manager = SimpleNamespace(logdir=tmp_path)
    full = "HEAD" + "x" * 5000 + "TAIL"
    with patch("gptme.logmanager.LogManager.get_current_log", return_value=manager):
        bounded = _bound_browser_result(full, "read_url")

    assert bounded.startswith("HEAD") and bounded.endswith("TAIL")
    assert len(bounded) < 300
    assert f"{len(full) - 100} characters omitted" in bounded
    saved = [p for p in (tmp_path / "tool-outputs").rglob("*") if p.is_file()]
    assert len(saved) == 1 and saved[0].read_text() == full
    assert str(saved[0]) in bounded
    assert "browser" in str(saved[0])


def test_bound_browser_result_still_bounds_without_a_conversation_dir(monkeypatch):
    monkeypatch.setenv(BROWSER_MAX_OUTPUT_CHARS_ENV, "100")
    with patch("gptme.logmanager.LogManager.get_current_log", return_value=None):
        bounded = _bound_browser_result("x" * 5000, "read_url")
    assert len(bounded) < 300 and "4900 characters omitted" in bounded
    assert "saved to" not in bounded


def test_bound_browser_result_invalid_env_falls_back_to_default(monkeypatch):
    for bad in ("abc", "0", "-5"):
        monkeypatch.setenv(BROWSER_MAX_OUTPUT_CHARS_ENV, bad)
        assert _browser_max_output_chars() == _DEFAULT_BROWSER_MAX_OUTPUT_CHARS


def test_bound_browser_result_still_bounds_when_save_fails(monkeypatch, tmp_path):
    monkeypatch.setenv(BROWSER_MAX_OUTPUT_CHARS_ENV, "100")
    manager = SimpleNamespace(logdir=tmp_path)
    with (
        patch("gptme.logmanager.LogManager.get_current_log", return_value=manager),
        patch(
            "gptme.util.output_storage.save_large_output",
            side_effect=OSError("disk full"),
        ),
    ):
        bounded = _bound_browser_result("HEAD" + "x" * 5000 + "TAIL", "read_url")
    assert bounded.startswith("HEAD") and bounded.endswith("TAIL")
    assert "characters omitted" in bounded
    assert "saved to" not in bounded
    assert not (tmp_path / "tool-outputs").exists()


def test_read_url_bounds_an_oversized_page(monkeypatch, tmp_path):
    """The public function, not just the helper, must apply the bound."""
    monkeypatch.setenv(BROWSER_MAX_OUTPUT_CHARS_ENV, "100")
    full = "HEAD" + "y" * 5000 + "TAIL"
    manager = SimpleNamespace(logdir=tmp_path)
    with (
        patch("gptme.tools.browser._is_pdf_url", return_value=False),
        patch("gptme.tools.browser.parse_github_url", return_value=None),
        patch("gptme.tools.browser._is_github_repo_url", return_value=False),
        patch("gptme.tools.browser._read_url_with_browser", return_value=full),
        patch("gptme.logmanager.LogManager.get_current_log", return_value=manager),
    ):
        bounded = read_url("https://example.com")
    assert bounded.startswith("HEAD") and bounded.endswith("TAIL")
    assert len(bounded) < 300
    saved = [p for p in (tmp_path / "tool-outputs").rglob("*") if p.is_file()]
    assert len(saved) == 1 and saved[0].read_text() == full
    assert str(saved[0]) in bounded


def test_native_from_function_conversion_bounds_browser_results(monkeypatch, tmp_path):
    """Native subtoolspecs call the wrapped function; they must not dump the page."""
    monkeypatch.setenv(BROWSER_MAX_OUTPUT_CHARS_ENV, "100")
    full = "HEAD" + "z" * 5000 + "TAIL"
    manager = SimpleNamespace(logdir=tmp_path)
    spec = next(
        s for s in tool.as_function_subtoolspecs() if s.name == "browser.read_url"
    )
    assert spec.execute is not None
    with (
        patch("gptme.tools.browser._is_pdf_url", return_value=False),
        patch("gptme.tools.browser.parse_github_url", return_value=None),
        patch("gptme.tools.browser._is_github_repo_url", return_value=False),
        patch("gptme.tools.browser._read_url_with_browser", return_value=full),
        patch("gptme.logmanager.LogManager.get_current_log", return_value=manager),
    ):
        messages = _as_messages(
            spec.execute(None, None, {"url": "https://example.com"})
        )
    assert len(messages) == 1
    text = messages[0].content
    assert text.startswith("HEAD") and text.endswith("TAIL") and len(text) < 300
    assert "characters omitted" in text
    saved = [p for p in (tmp_path / "tool-outputs").rglob("*") if p.is_file()]
    assert len(saved) == 1 and saved[0].read_text() == full


def test_from_function_generic_path_is_unbounded_without_adapter():
    """Control: a non-browser from_function tool still returns the full string.

    The browser adapter is the entry-point wrapper; this documents that the
    generic native conversion itself does not cap results.
    """

    def dump(text: str) -> str:
        """Return text unchanged."""
        return text

    spec = ToolSpec.from_function(dump)
    assert spec.execute is not None
    huge = "x" * 5000
    messages = _as_messages(spec.execute(None, None, {"text": huge}))
    assert messages[0].content == huge
