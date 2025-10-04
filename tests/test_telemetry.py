"""Tests for telemetry functionality."""

import socket
from unittest.mock import patch

import pytest


def test_find_available_port():
    """Test that _find_available_port finds an available port."""
    # Import here to avoid requiring telemetry dependencies for all tests
    from gptme.util._telemetry import _find_available_port

    # Should find port 8000 if available
    port = _find_available_port(8000, "localhost")
    assert port is not None
    assert 8000 <= port < 8010


def test_find_available_port_with_conflict():
    """Test that _find_available_port finds next available port when first is in use."""
    from gptme.util._telemetry import _find_available_port

    # Bind and listen on port 8000 to simulate conflict
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 8000))
        sock.listen(1)

        # Should find port 8001 or later
        port = _find_available_port(8000, "localhost")
        assert port is not None
        assert port > 8000


def test_find_available_port_none_available():
    """Test that _find_available_port returns None when no ports available."""
    from gptme.util._telemetry import _find_available_port

    # Bind and listen on ports 8000-8009 to simulate no ports available
    sockets = []
    try:
        for i in range(10):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("localhost", 8000 + i))
            sock.listen(1)
            sockets.append(sock)

        # Should return None
        port = _find_available_port(8000, "localhost", max_attempts=10)
        assert port is None
    finally:
        for sock in sockets:
            sock.close()


@pytest.mark.skipif(
    not pytest.importorskip(
        "opentelemetry", reason="telemetry dependencies not installed"
    ),
    reason="Requires telemetry dependencies",
)
def test_init_telemetry_port_conflict():
    """Test that init_telemetry handles port conflicts gracefully."""
    from gptme.util._telemetry import init_telemetry

    # Mock the console to avoid output during tests
    with patch("gptme.util.console.log"):
        # Bind and listen on port 8000 to simulate conflict
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("localhost", 8000))
            sock.listen(1)

            # Mock environment to enable telemetry
            with patch.dict("os.environ", {"GPTME_TELEMETRY_ENABLED": "true"}):
                # Should succeed by finding an alternative port
                init_telemetry(prometheus_port=8000)


@pytest.mark.skipif(
    not pytest.importorskip(
        "opentelemetry", reason="telemetry dependencies not installed"
    ),
    reason="Requires telemetry dependencies",
)
def test_init_telemetry_no_ports_available():
    """Test that init_telemetry works when no Prometheus ports available."""
    from gptme.util._telemetry import init_telemetry

    # Mock _find_available_port to return None (no ports available)
    with patch("gptme.util._telemetry._find_available_port", return_value=None):
        with patch("gptme.util.console.log"):
            with patch.dict("os.environ", {"GPTME_TELEMETRY_ENABLED": "true"}):
                # Should succeed with Prometheus disabled but OTLP tracing enabled
                init_telemetry(prometheus_port=8000)
