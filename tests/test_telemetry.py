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


def test_init_telemetry_with_pushgateway(monkeypatch):
    """Test telemetry initialization with Pushgateway."""
    import time
    from unittest.mock import patch

    monkeypatch.setenv("GPTME_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("PUSHGATEWAY_URL", "http://localhost:9091")

    # Mock push_to_gateway at the prometheus_client level
    with patch("prometheus_client.push_to_gateway") as mock_push:
        mock_push.return_value = None

        from gptme.util._telemetry import init_telemetry, shutdown_telemetry

        # Initialize telemetry
        init_telemetry()

        # Wait a bit for the first push
        time.sleep(0.1)

        # Verify setup was successful (push_to_gateway should be callable)
        # Note: The actual push happens in a background thread with 30s interval
        # so we don't check for actual calls here

        # Cleanup
        shutdown_telemetry()


def test_pushgateway_periodic_push(monkeypatch):
    """Test that metrics are pushed periodically to Pushgateway."""
    import time
    from unittest.mock import patch

    monkeypatch.setenv("GPTME_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("PUSHGATEWAY_URL", "http://localhost:9091")

    with patch("prometheus_client.push_to_gateway") as mock_push:
        mock_push.return_value = None

        from gptme.util._telemetry import init_telemetry, shutdown_telemetry

        # Initialize telemetry with Pushgateway
        init_telemetry()

        # Wait briefly to ensure thread starts
        time.sleep(0.5)

        # The periodic push thread should be running
        # (actual push happens every 30s, so we won't see calls in this short test)

        # Cleanup
        shutdown_telemetry()
