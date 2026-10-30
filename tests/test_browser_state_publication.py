"""Browser authentication state stays private during failure-safe publication."""

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("playwright")

from gptme.tools import _browser_playwright as bp


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("existing", [False, True])
def test_state_private_before_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    target = tmp_path / "state.json"
    if existing:
        target.write_text("OLD")
        target.chmod(0o644)
    context = MagicMock()

    def serialize(*, path: str) -> None:
        staging = Path(path)
        assert staging.exists()
        assert stat.S_IMODE(staging.stat().st_mode) == 0o600
        staging.write_text('{"cookies":[],"origins":[]}')
        assert stat.S_IMODE(staging.stat().st_mode) == 0o600
        assert target.read_text() == "OLD" if existing else not target.exists()

    context.storage_state.side_effect = serialize
    monkeypatch.setattr(bp, "_current_context", context)
    previous = os.umask(0)
    try:
        result = bp._do_save_browser_state(MagicMock(), str(target))
    finally:
        os.umask(previous)
    assert str(target) in result
    assert target.read_text() == '{"cookies":[],"origins":[]}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["permission", "serialize", "publish"])
def test_state_failure_preserves_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool, failure: str
) -> None:
    target = tmp_path / "state.json"
    if existing:
        target.write_text("OLD")
    context = MagicMock()

    def serialize(*, path: str) -> None:
        Path(path).write_text("PARTIAL")
        if failure == "serialize":
            raise OSError("serialization failed")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected failure")

    context.storage_state.side_effect = serialize
    monkeypatch.setattr(bp, "_current_context", context)
    if failure == "permission":
        monkeypatch.setattr(bp.os, "chmod", fail)
    elif failure == "publish":
        monkeypatch.setattr(bp.os, "replace", fail)
    with pytest.raises(OSError, match="serialization failed|injected failure"):
        bp._do_save_browser_state(MagicMock(), str(target))
    assert target.read_text() == "OLD" if existing else not target.exists()
    assert list(tmp_path.iterdir()) == ([target] if existing else [])
    if failure == "permission":
        context.storage_state.assert_not_called()


def test_state_publication_replaces_symlink_not_referent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requires_symlinks: None
) -> None:
    referent = tmp_path / "unrelated.json"
    referent.write_text("UNRELATED")
    before = referent.stat().st_mode
    target = tmp_path / "state.json"
    target.symlink_to(referent)
    context = MagicMock()
    context.storage_state.side_effect = lambda *, path: Path(path).write_text("NEW")
    monkeypatch.setattr(bp, "_current_context", context)
    bp._do_save_browser_state(MagicMock(), str(target))
    assert not target.is_symlink()
    assert target.read_text() == "NEW"
    assert referent.read_text() == "UNRELATED"
    assert referent.stat().st_mode == before
    assert set(tmp_path.iterdir()) == {referent, target}


@pytest.mark.parametrize("existing", [False, True])
def test_state_roundtrip_with_real_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
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
                pytest.skip("Playwright Chromium is not installed")
            executable = shells[-1]
        browser = pw.chromium.launch(headless=True, executable_path=str(executable))
        try:
            context = browser.new_context()
            context.add_cookies(
                [
                    {
                        "name": "synthetic",
                        "value": "not-a-secret",
                        "domain": "example.test",
                        "path": "/",
                    }
                ]
            )
            page = context.new_page()
            page.route("**/*", lambda route: route.fulfill(body="<html></html>"))
            page.goto("https://example.test/")
            page.evaluate("localStorage.setItem('synthetic', 'local-value')")
            target = tmp_path / "state.json"
            if existing:
                target.write_text("OLD")
            monkeypatch.setattr(bp, "_current_context", context)
            bp._do_save_browser_state(browser, str(target))
            restored = browser.new_context(storage_state=str(target))
            assert any(
                cookie["name"] == "synthetic" and cookie["value"] == "not-a-secret"
                for cookie in restored.cookies()
            )
            restored_page = restored.new_page()
            restored_page.route(
                "**/*", lambda route: route.fulfill(body="<html></html>")
            )
            restored_page.goto("https://example.test/")
            assert restored_page.evaluate("localStorage.getItem('synthetic')") == (
                "local-value"
            )
            restored.close()
            context.close()
        finally:
            browser.close()
