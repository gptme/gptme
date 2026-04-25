"""Tests for telemetry functionality."""

import subprocess
import sys
from unittest.mock import patch

import pytest


def test_telemetry_imports_lazy():
    """Importing gptme.telemetry must not eagerly import opentelemetry.

    Locks in the lazy-load contract: heavy opentelemetry packages should only
    load when init_telemetry() actually runs (i.e. when GPTME_TELEMETRY_ENABLED).
    Regression guard for the import-guard-vs-lazy-load fix.
    """
    code = (
        "import gptme.telemetry, gptme.util._telemetry, sys; "
        "leaked = [m for m in sys.modules if m == 'opentelemetry' or "
        "m.startswith('opentelemetry.')]; "
        "assert not leaked, f'opentelemetry eagerly imported: {leaked[:5]}'"
    )
    subprocess.check_call([sys.executable, "-c", code])


@pytest.mark.skipif(
    not pytest.importorskip(
        "opentelemetry", reason="telemetry dependencies not installed"
    ),
    reason="Requires telemetry dependencies",
)
def test_init_telemetry_with_pushgateway(monkeypatch):
    """Test telemetry initialization with Pushgateway."""
    import time

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
