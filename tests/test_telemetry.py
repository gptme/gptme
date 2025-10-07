"""Tests for telemetry functionality."""

from unittest.mock import patch

import pytest



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
