"""Interactive navigation must keep live state, without sharing one-shot contexts."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("playwright")

from gptme.tools import _browser_playwright as pw


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    monkeypatch.setattr(pw, "_browser", None)
    monkeypatch.setattr(pw, "_current_page", None)
    monkeypatch.setattr(pw, "_current_context", None)
    monkeypatch.setattr(pw, "get_context_options", lambda: {})
    monkeypatch.setattr(pw, "_page_snapshot", lambda: "snapshot")
    browser = MagicMock()
    page = browser.new_context.return_value.new_page.return_value
    page.is_closed.return_value = False
    page.url = "https://example.com/account"
    return browser, page


def test_direct_navigation_reuses_live_page_and_context(
    interactive: tuple[MagicMock, MagicMock],
) -> None:
    browser, page = interactive
    pw._open_page(browser, "https://example.com/login")
    context = browser.new_context.return_value
    pw._open_page(browser, "https://example.com/account")

    assert pw._current_page is page
    assert pw._current_context is context
    browser.new_context.assert_called_once()
    page.close.assert_not_called()
    assert isinstance(context, MagicMock)
    context.close.assert_not_called()
    assert [call.args[0] for call in page.goto.call_args_list] == [
        "https://example.com/login",
        "https://example.com/account",
    ]


@pytest.mark.parametrize("closed", [False, True])
def test_explicit_or_external_close_starts_fresh(
    interactive: tuple[MagicMock, MagicMock], closed: bool
) -> None:
    browser, page = interactive
    pw._open_page(browser, "https://example.com/login")
    context = browser.new_context.return_value
    if closed:
        page.is_closed.return_value = True
    else:
        pw._do_close_page(browser)
    pw._open_page(browser, "https://example.com/account")
    assert browser.new_context.call_count == 2
    context.close.assert_called_once()


@pytest.mark.parametrize("disallowed", [False, True])
def test_failed_navigation_cleans_up_owned_session(
    interactive: tuple[MagicMock, MagicMock], disallowed: bool
) -> None:
    browser, page = interactive
    pw._open_page(browser, "https://example.com/login")
    context = browser.new_context.return_value
    error: type[Exception]
    if disallowed:
        from gptme.tools._url_safety import set_session_allow_hosts

        set_session_allow_hosts(["example.com"])
        page.url = "https://blocked.test/"
        error = ValueError
    else:
        page.goto.side_effect = RuntimeError("navigation failed")
        error = RuntimeError
    with pytest.raises(error):
        pw._open_page(browser, "https://example.com/account")
    assert pw._current_page is None
    assert pw._current_context is None
    page.close.assert_called_once()
    context.close.assert_called_once()


def test_shared_cdp_context_is_not_owned_by_page(
    interactive: tuple[MagicMock, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise ownership branch only, not real CDP transport."""
    browser, _ = interactive
    context = MagicMock()
    page = context.new_page.return_value
    page.is_closed.return_value = False
    page.url = "https://example.com/account"
    monkeypatch.setattr(
        pw, "_browser", SimpleNamespace(cdp_url="fixture", _session_context=context)
    )
    pw._open_page(browser, "https://example.com/login")
    pw._open_page(browser, "https://example.com/account")
    assert pw._current_context is None
    context.new_page.assert_called_once()
    browser.new_context.assert_not_called()
    pw._do_close_page(browser)
    page.close.assert_called_once()
    context.close.assert_not_called()


def test_one_shot_page_does_not_share_interactive_context(
    interactive: tuple[MagicMock, MagicMock],
) -> None:
    browser, page = interactive
    pw._open_page(browser, "https://example.com/login")
    managed = pw._create_page(browser)
    assert browser.new_context.call_count == 2
    assert pw._current_page is page
    managed.close()
