"""Candidate state failures must leave the active browser session usable."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("playwright")

from gptme.tools import _browser_playwright as bp
from gptme.tools import _browser_thread as bt


@pytest.mark.parametrize(
    "content", ["{broken", "[]", '{"cookies":42}', '{"origins":false}']
)
def test_invalid_state_preserves_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text(content)
    previous = tmp_path / "previous.json"
    page, context = MagicMock(), MagicMock()
    monkeypatch.setattr(bp, "_current_page", page)
    monkeypatch.setattr(bp, "_current_context", context)
    monkeypatch.setattr(bt, "_override_storage_state", previous)
    with pytest.raises(
        ValueError, match="Expecting property name|cookie and origin lists"
    ):
        bp._do_load_browser_state(None, str(candidate))
    assert bp._current_page is page
    assert bp._current_context is context
    page.close.assert_not_called()
    context.close.assert_not_called()
    assert bt._override_storage_state == previous


@pytest.mark.parametrize("cdp", [False, True])
def test_context_preparation_failure_preserves_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cdp: bool
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"cookies":[],"origins":[]}')
    page, context = MagicMock(), MagicMock()
    previous = tmp_path / "previous.json"
    thread = SimpleNamespace(
        cdp_url="http://localhost:9222" if cdp else None, _session_context=context
    )
    browser = MagicMock()
    browser.new_context.side_effect = RuntimeError("cannot prepare context")
    monkeypatch.setattr(bp, "_browser", thread)
    monkeypatch.setattr(bp, "_current_page", page)
    monkeypatch.setattr(bp, "_current_context", context)
    monkeypatch.setattr(bt, "_override_storage_state", previous)
    with pytest.raises(RuntimeError, match="cannot prepare context"):
        bp._do_load_browser_state(browser, str(candidate))
    assert bp._current_page is page
    assert bp._current_context is context
    assert thread._session_context is context
    page.close.assert_not_called()
    context.close.assert_not_called()
    assert bt._override_storage_state == previous


@pytest.mark.parametrize("missing", [False, True])
def test_unreadable_state_preserves_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    candidate = tmp_path / "candidate.json"
    if not missing:
        candidate.write_text("{}")
        original = Path.read_text

        def read(path: Path, *args, **kwargs):
            if path == candidate:
                raise PermissionError("cannot read candidate")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
    close = MagicMock()
    monkeypatch.setattr(bp, "_close_current_page", close)
    previous = tmp_path / "previous.json"
    monkeypatch.setattr(bt, "_override_storage_state", previous)
    with pytest.raises(
        OSError, match="cannot read candidate|Browser state file not found"
    ):
        bp._do_load_browser_state(None, str(candidate))
    close.assert_not_called()
    assert bt._override_storage_state == previous


@pytest.mark.parametrize("cdp", [False, True])
def test_real_browser_preserves_draft_and_loads_valid_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cdp: bool
) -> None:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        executable = Path(pw.chromium.executable_path)
        if not executable.exists():
            shells = sorted(
                Path.home().glob(
                    ".cache/ms-playwright/chromium_headless_shell-*/"
                    "chrome-headless-shell-linux64/chrome-headless-shell"
                )
            )
            if not shells:
                pytest.skip("Playwright Chromium not installed")
            executable = shells[-1]
        browser = pw.chromium.launch(headless=True, executable_path=str(executable))
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_content('<input id="draft" value="keep me">')
            thread = SimpleNamespace(
                cdp_url="http://localhost:9222" if cdp else None,
                _session_context=context if cdp else None,
            )
            monkeypatch.setattr(bp, "_browser", thread)
            monkeypatch.setattr(bp, "_current_page", page)
            monkeypatch.setattr(bp, "_current_context", None if cdp else context)
            previous = tmp_path / "previous.json"
            monkeypatch.setattr(bt, "_override_storage_state", previous)
            candidate = tmp_path / "candidate.json"
            for content in ("{broken", "[]", '{"cookies":[{}]}', '{"origins":[{}]}'):
                candidate.write_text(content)
                with pytest.raises(
                    (ValueError, PlaywrightError),
                    match="Expecting property name|cookie and origin lists|expected",
                ):
                    bp._do_load_browser_state(browser, str(candidate))
                assert not page.is_closed()
                assert page.locator("#draft").input_value() == "keep me"
                assert bt._override_storage_state == previous
                assert bp._current_context is (None if cdp else context)
            state = {
                "cookies": [
                    {
                        "name": "synthetic",
                        "value": "not-secret",
                        "domain": "example.test",
                        "path": "/",
                        "expires": -1,
                        "httpOnly": False,
                        "secure": False,
                        "sameSite": "Lax",
                    }
                ],
                "origins": [
                    {
                        "origin": "https://example.test",
                        "localStorage": [{"name": "synthetic", "value": "local-value"}],
                    }
                ],
            }
            candidate.write_text(json.dumps(state))
            result = bp._do_load_browser_state(browser, str(candidate))
            assert "loaded" in result
            assert page.is_closed()
            assert bt._override_storage_state == candidate
            create_page = bp._create_page

            def routed_page(*args, **kwargs):
                managed = create_page(*args, **kwargs)
                managed.page.route(
                    "**/*", lambda route: route.fulfill(body="<html>Restored</html>")
                )
                return managed

            monkeypatch.setattr(bp, "_create_page", routed_page)
            prepared = thread._session_context
            bp._open_page(browser, "https://example.test/")
            restored_page = bp._current_page
            assert restored_page is not None
            restored = restored_page.context
            if cdp:
                assert restored is prepared
                assert bp._current_context is None
            else:
                assert restored is bp._current_context
            assert any(
                cookie["name"] == "synthetic" and cookie["value"] == "not-secret"
                for cookie in restored.cookies()
            )
            assert (
                restored_page.evaluate("localStorage.getItem('synthetic')")
                == "local-value"
            )
            restored.close()
        finally:
            browser.close()


def test_validation_context_close_failure_preserves_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"cookies":[],"origins":[]}')
    previous = tmp_path / "previous.json"
    page, context, browser = MagicMock(), MagicMock(), MagicMock()
    browser.new_context.return_value.close.side_effect = RuntimeError(
        "cannot close validation"
    )
    monkeypatch.setattr(bp, "_browser", None)
    monkeypatch.setattr(bp, "_current_page", page)
    monkeypatch.setattr(bp, "_current_context", context)
    monkeypatch.setattr(bt, "_override_storage_state", previous)
    with pytest.raises(RuntimeError, match="cannot close validation"):
        bp._do_load_browser_state(browser, str(candidate))
    assert bp._current_page is page
    assert bp._current_context is context
    page.close.assert_not_called()
    context.close.assert_not_called()
    assert bt._override_storage_state == previous
