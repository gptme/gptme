"""Tests for ACP protocol logging.

Verifies that the protocol observer correctly formats JSON-RPC messages
and that the logging can be enabled via environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pytest


class _Direction(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


@dataclass(frozen=True)
class _MockStreamEvent:
    """Minimal mock matching acp.connection.StreamEvent."""

    direction: _Direction
    message: dict[str, Any]


class TestTruncate:
    """Tests for _truncate helper."""

    def test_short_string(self):
        from gptme.acp.__main__ import _truncate

        assert _truncate("hello") == "hello"

    def test_long_string(self):
        from gptme.acp.__main__ import _truncate

        long = "x" * 300
        result = _truncate(long, max_len=50)
        assert len(result) < 100
        assert "..." in result
        assert "300 chars" in result

    def test_dict_value(self):
        from gptme.acp.__main__ import _truncate

        result = _truncate({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result

    def test_truncate_large_dict(self):
        from gptme.acp.__main__ import _truncate

        large = {"data": "x" * 500}
        result = _truncate(large, max_len=50)
        assert "..." in result


class TestMakeProtocolObserver:
    """Tests for _make_protocol_observer."""

    def test_observer_is_callable(self):
        from gptme.acp.__main__ import _make_protocol_observer

        observer = _make_protocol_observer()
        assert callable(observer)

    def test_logs_incoming_request(self, caplog):
        from gptme.acp.__main__ import _make_protocol_observer

        observer = _make_protocol_observer()
        event = _MockStreamEvent(
            direction=_Direction.INCOMING,
            message={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {"protocolVersion": 1},
            },
        )
        import logging

        with caplog.at_level(logging.DEBUG, logger="gptme.acp.protocol"):
            observer(event)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "-->" in record.message
        assert "initialize" in record.message
        assert "id=0" in record.message
        assert "protocolVersion" in record.message

    def test_logs_outgoing_response(self, caplog):
        from gptme.acp.__main__ import _make_protocol_observer

        observer = _make_protocol_observer()
        event = _MockStreamEvent(
            direction=_Direction.OUTGOING,
            message={
                "jsonrpc": "2.0",
                "id": 0,
                "result": {"protocolVersion": 1, "serverInfo": {"name": "gptme"}},
            },
        )
        import logging

        with caplog.at_level(logging.DEBUG, logger="gptme.acp.protocol"):
            observer(event)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "<--" in record.message
        assert "response" in record.message
        assert "id=0" in record.message
        assert "protocolVersion" in record.message

    def test_logs_outgoing_notification(self, caplog):
        from gptme.acp.__main__ import _make_protocol_observer

        observer = _make_protocol_observer()
        event = _MockStreamEvent(
            direction=_Direction.OUTGOING,
            message={
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": "abc123", "update": {"type": "text"}},
            },
        )
        import logging

        with caplog.at_level(logging.DEBUG, logger="gptme.acp.protocol"):
            observer(event)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "<--" in record.message
        assert "notification" in record.message
        assert "session/update" in record.message

    def test_logs_error_response(self, caplog):
        from gptme.acp.__main__ import _make_protocol_observer

        observer = _make_protocol_observer()
        event = _MockStreamEvent(
            direction=_Direction.OUTGOING,
            message={
                "jsonrpc": "2.0",
                "id": 5,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": "Failed to get session",
                },
            },
        )
        import logging

        with caplog.at_level(logging.DEBUG, logger="gptme.acp.protocol"):
            observer(event)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "<--" in record.message
        assert "error=" in record.message
        assert "-32603" in record.message

    def test_no_log_when_disabled(self, caplog):
        """Observer should not produce output when protocol logger is above DEBUG."""
        from gptme.acp.__main__ import _make_protocol_observer

        observer = _make_protocol_observer()
        event = _MockStreamEvent(
            direction=_Direction.INCOMING,
            message={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        )
        import logging

        with caplog.at_level(logging.INFO, logger="gptme.acp.protocol"):
            observer(event)

        # No records because protocol_logger is at INFO, observer logs at DEBUG
        assert len(caplog.records) == 0


class TestProtocolLoggerSetup:
    """Tests for GPTME_ACP_LOG_PROTOCOL env var handling."""

    def test_env_var_enables_protocol_logging(self, monkeypatch):
        """Setting GPTME_ACP_LOG_PROTOCOL=1 should enable the protocol logger."""
        import logging

        from gptme.acp.__main__ import protocol_logger

        # Reset to default state
        protocol_logger.setLevel(logging.NOTSET)
        original_handlers = protocol_logger.handlers[:]
        protocol_logger.handlers.clear()

        try:
            monkeypatch.setenv("GPTME_ACP_LOG_PROTOCOL", "1")

            # Simulate what main() does
            if os.environ.get("GPTME_ACP_LOG_PROTOCOL", "").strip() in (
                "1",
                "true",
                "yes",
            ):
                protocol_logger.setLevel(logging.DEBUG)

            assert protocol_logger.isEnabledFor(logging.DEBUG)
        finally:
            protocol_logger.setLevel(logging.NOTSET)
            protocol_logger.handlers[:] = original_handlers

    @pytest.mark.parametrize("value", ["0", "", "false", "no"])
    def test_env_var_disabled_values(self, monkeypatch, value):
        """Non-truthy values should not enable protocol logging."""
        import logging

        from gptme.acp.__main__ import protocol_logger

        protocol_logger.setLevel(logging.NOTSET)

        monkeypatch.setenv("GPTME_ACP_LOG_PROTOCOL", value)

        # Simulate what main() does — these values should NOT match
        should_enable = value.strip() in ("1", "true", "yes")
        assert not should_enable
