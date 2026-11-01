"""CDP reconnects must create contexts in the retried command's owner scope."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("playwright")

from gptme.hooks import current_conversation_id
from gptme.tools import _browser_playwright as bp
from gptme.tools import _browser_thread as bt


def test_cdp_reconnect_uses_command_owner_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the real queue/retry boundary, not just direct page creation."""
    monkeypatch.setattr(bt, "_override_storage_state", None)
    monkeypatch.setattr(bt, "_conversation_storage_states", {}, raising=False)
    monkeypatch.setattr(bp, "_current_page", None)
    monkeypatch.setattr(bp, "_current_context", None)
    monkeypatch.setattr(bp, "_session_owner", None)
    cli_state = tmp_path / "cli.json"
    conversation_state = tmp_path / "conv-a.json"
    for state in (cli_state, conversation_state):
        state.write_text('{"cookies": [], "origins": []}')

    browsers = [MagicMock(name="first_browser"), MagicMock(name="reconnected_browser")]
    # Fail *after* _create_page has stamped conv-a as the session owner.
    browsers[0].new_context.return_value.new_page.side_effect = RuntimeError(
        "connection closed"
    )
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.side_effect = browsers
    monkeypatch.setattr(bt, "sync_playwright", lambda: playwright)
    playwright.start.return_value = playwright
    observed: list[tuple[str | None, dict[str, Any]]] = []
    context = browsers[1].new_context.return_value

    def new_context(**options: Any) -> Any:
        observed.append((current_conversation_id.get(), options))
        return context

    browsers[1].new_context.side_effect = new_context
    token = current_conversation_id.set(None)
    thread = None
    try:
        bt.set_storage_state_override(cli_state)
        current_conversation_id.set("conv-a")
        bt.set_storage_state_override(conversation_state)
        thread = bt.BrowserThread(cdp_url="http://127.0.0.1:9222")
        monkeypatch.setattr(bp, "_browser", thread)

        managed = thread.execute(bp._create_page)

        assert managed.page is context.new_page.return_value
        assert len(observed) == 1
        owner, options = observed[0]
        assert options["storage_state"] == str(conversation_state)
        assert owner == "conv-a"
        assert bp._session_owner == "conv-a"
        assert thread._session_context is context
        browsers[0].new_context.return_value.close.assert_called_once()
    finally:
        if thread is not None:
            thread.stop()
        current_conversation_id.reset(token)
