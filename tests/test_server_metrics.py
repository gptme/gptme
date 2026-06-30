"""Tests for the Prometheus metrics endpoint."""

import pytest

flask = pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)


@pytest.fixture()
def metrics_client():
    """Flask test client with metrics enabled."""
    from gptme.server.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_metrics_endpoint_returns_prometheus_format(metrics_client):
    """GET /api/v0/metrics returns Prometheus text format."""
    resp = metrics_client.get("/api/v0/metrics")
    assert resp.status_code == 200
    content_type = resp.content_type
    assert "text/plain" in content_type
    data = resp.get_data(as_text=True)
    # Should contain at least the help line for the requests counter
    assert "gptme_server_requests_total" in data


def test_metrics_endpoint_records_requests(metrics_client):
    """Making requests causes the request counter to be incremented."""
    # Make a real API request
    metrics_client.get("/api/v2/conversations")
    # Now check metrics
    resp = metrics_client.get("/api/v0/metrics")
    data = resp.get_data(as_text=True)
    # The conversations endpoint should have been counted
    assert "gptme_server_requests_total" in data
    # Counter should have at least one sample
    assert "gptme_server_requests_total{" in data


def test_metrics_endpoint_not_self_counted(metrics_client):
    """Hits to /api/v0/metrics should NOT be counted in the request counter."""
    # Hit metrics endpoint a few times
    for _ in range(3):
        metrics_client.get("/api/v0/metrics")

    resp = metrics_client.get("/api/v0/metrics")
    data = resp.get_data(as_text=True)
    # The metrics endpoint itself should not appear as a labelled series
    assert "/api/v0/metrics" not in data


def test_metrics_helper_functions():
    """Helper functions are safe when prometheus_client is available."""
    from gptme.server.metrics import (
        record_cache_result,
        sse_connection_close,
        sse_connection_open,
        update_conversation_metrics,
    )

    # Should not raise even when called multiple times
    record_cache_result(hit=True)
    record_cache_result(hit=False)
    update_conversation_metrics(10, 100)
    sse_connection_open()
    sse_connection_close()


def test_metrics_404_uses_sentinel_endpoint(metrics_client):
    """Unmatched routes use the '<not_found>' sentinel to prevent cardinality explosion."""
    # Hit a path that matches no route
    metrics_client.get("/this/path/does/not/exist/at/all")
    resp = metrics_client.get("/api/v0/metrics")
    data = resp.get_data(as_text=True)
    # Raw path must NOT appear as a Prometheus label value
    assert "/this/path/does/not/exist/at/all" not in data
    # The sentinel must be used instead
    assert "<not_found>" in data


def test_metrics_module_graceful_without_prometheus(monkeypatch):
    """Metrics helpers are no-ops when prometheus_client is not installed."""
    import gptme.server.metrics as m

    orig = m._available
    try:
        m._available = False
        # All public helpers should be silent no-ops when disabled (no exception raised)
        m.record_cache_result(hit=True)
        m.update_conversation_metrics(5, 50)
        m.sse_connection_open()
        m.sse_connection_close()
    finally:
        m._available = orig
