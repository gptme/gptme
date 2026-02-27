"""Tests for gptme ACP client module (gptme/acp/client.py).

Tests focus on the client-side logic:
- _MinimalClient callback implementations
- GptmeAcpClient interface & error handling
- acp_client() context manager helper

Integration tests (spawning a real gptme-acp subprocess) are marked slow
and require the acp extra to be installed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _skip_if_no_acp():
    try:
        import acp  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("acp package not installed")


# ---------------------------------------------------------------------------
# _MinimalClient
# ---------------------------------------------------------------------------


class TestMinimalClient:
    """Unit tests for _MinimalClient — no subprocess needed."""

    def setup_method(self):
        _skip_if_no_acp()
        from gptme.acp.client import _MinimalClient

        self.MinimalClient = _MinimalClient

    # -- session_update -------------------------------------------------------

    def test_session_update_no_callback(self):
        client = self.MinimalClient()
        _run(client.session_update("sess-1", MagicMock()))  # should not raise

    def test_session_update_invokes_callback(self):
        received = []
        client = self.MinimalClient(
            on_update=lambda sid, upd: received.append((sid, upd))
        )
        update = MagicMock()
        _run(client.session_update("sess-42", update))
        assert received == [("sess-42", update)]

    def test_session_update_invokes_async_callback(self):
        received = []

        async def on_update(sid, upd):
            received.append((sid, upd))

        client = self.MinimalClient(on_update=on_update)
        update = MagicMock()
        _run(client.session_update("sess-async", update))
        assert received == [("sess-async", update)]

    # -- request_permission ---------------------------------------------------

    def test_request_permission_auto_confirm_true(self):
        from acp.schema import (  # type: ignore[import-not-found]
            AllowedOutcome,
            PermissionOption,
            RequestPermissionResponse,
        )

        client = self.MinimalClient(auto_confirm=True)
        # PermissionOptionKind is Literal[...], not an enum — use a string value directly
        option = PermissionOption(
            option_id="opt-allow",
            name="Allow once",
            kind="allow_once",
        )
        resp = _run(
            client.request_permission(
                options=[option],
                session_id="sess-1",
                tool_call=MagicMock(),
            )
        )
        assert isinstance(resp, RequestPermissionResponse)
        assert isinstance(resp.outcome, AllowedOutcome)
        assert resp.outcome.option_id == "opt-allow"

    def test_request_permission_auto_confirm_false(self):
        from acp.schema import (  # type: ignore[import-not-found]
            DeniedOutcome,
            RequestPermissionResponse,
        )

        client = self.MinimalClient(auto_confirm=False)
        resp = _run(
            client.request_permission(
                options=[],
                session_id="sess-1",
                tool_call=MagicMock(),
            )
        )
        assert isinstance(resp, RequestPermissionResponse)
        assert isinstance(resp.outcome, DeniedOutcome)

    def test_request_permission_no_options(self):
        """With no options, client should still return an AllowedOutcome."""
        from acp.schema import (  # type: ignore[import-not-found]
            AllowedOutcome,
            RequestPermissionResponse,
        )

        client = self.MinimalClient(auto_confirm=True)
        resp = _run(
            client.request_permission(
                options=[],
                session_id="sess-1",
                tool_call=MagicMock(),
            )
        )
        assert isinstance(resp, RequestPermissionResponse)
        assert isinstance(resp.outcome, AllowedOutcome)

    # -- write_text_file / read_text_file ------------------------------------

    def test_write_and_read_text_file(self, tmp_path):
        client = self.MinimalClient()
        target = tmp_path / "subdir" / "hello.txt"

        _run(client.write_text_file("hello world", str(target), session_id="s"))
        assert target.read_text() == "hello world"

        resp = _run(client.read_text_file(str(target), session_id="s"))
        assert resp.content == "hello world"

    def test_read_text_file_missing(self, tmp_path):
        client = self.MinimalClient()
        resp = _run(
            client.read_text_file(str(tmp_path / "nonexistent.txt"), session_id="s")
        )
        assert resp.content == ""

    def test_read_text_file_with_line_limit(self, tmp_path):
        client = self.MinimalClient()
        target = tmp_path / "multi.txt"
        target.write_text("line1\nline2\nline3\nline4\n")

        resp = _run(client.read_text_file(str(target), session_id="s", line=2, limit=2))
        assert resp.content == "line2\nline3"

    # -- create_terminal (stub) ----------------------------------------------

    def test_create_terminal_returns_stub(self):
        client = self.MinimalClient()
        resp = _run(client.create_terminal("bash", session_id="s"))
        assert resp.terminal_id == "stub-terminal"


# ---------------------------------------------------------------------------
# GptmeAcpClient – interface & error path tests (no subprocess)
# ---------------------------------------------------------------------------


class TestGptmeAcpClientInterface:
    """Test GptmeAcpClient without launching a real subprocess."""

    def setup_method(self):
        _skip_if_no_acp()
        from gptme.acp.client import GptmeAcpClient

        self.GptmeAcpClient = GptmeAcpClient

    def test_defaults(self, tmp_path):
        client = self.GptmeAcpClient(workspace=tmp_path)
        assert client.workspace == tmp_path
        assert client.command == "gptme-acp"
        assert client.extra_args == []
        assert client._auto_confirm is True

    def test_custom_command(self, tmp_path):
        client = self.GptmeAcpClient(workspace=tmp_path, command="claude-acp")
        assert client.command == "claude-acp"

    def test_prompt_without_connect_raises(self, tmp_path):
        client = self.GptmeAcpClient(workspace=tmp_path)
        with pytest.raises(RuntimeError, match="not connected"):
            _run(client.prompt("fake-session", "hello"))

    def test_new_session_without_connect_raises(self, tmp_path):
        client = self.GptmeAcpClient(workspace=tmp_path)
        with pytest.raises(RuntimeError, match="not connected"):
            _run(client.new_session())

    def test_missing_command_raises(self, tmp_path):
        """Should raise FileNotFoundError if command isn't on PATH."""
        client = self.GptmeAcpClient(
            workspace=tmp_path, command="this-command-does-not-exist-xyz"
        )
        with pytest.raises(FileNotFoundError, match="not found"):
            _run(client.__aenter__())

    def test_workspace_defaults_to_cwd(self):
        import os

        client = self.GptmeAcpClient()
        assert client.workspace == Path(os.getcwd())


# ---------------------------------------------------------------------------
# acp_client() helper
# ---------------------------------------------------------------------------


class TestAcpClientHelper:
    def setup_method(self):
        _skip_if_no_acp()

    def test_acp_client_is_importable(self):
        from gptme.acp.client import acp_client

        assert callable(acp_client)

    def test_acp_client_exported_from_package(self):
        from gptme.acp import GptmeAcpClient, acp_client

        assert callable(acp_client)
        assert GptmeAcpClient is not None


# ---------------------------------------------------------------------------
# Module-level import guard
# ---------------------------------------------------------------------------


def test_client_module_importable():
    """gptme.acp.client should import cleanly without acp installed too."""
    import importlib

    mod = importlib.import_module("gptme.acp.client")
    assert hasattr(mod, "GptmeAcpClient")
    assert hasattr(mod, "acp_client")
    assert hasattr(mod, "_MinimalClient")
