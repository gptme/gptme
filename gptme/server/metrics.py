"""Prometheus metrics collection for gptme server.

Exposes a /api/v0/metrics endpoint in Prometheus text format.
Only active when ``prometheus_client`` is installed (included in the ``server`` extra).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import flask

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _available = True
except ImportError:
    _available = False

# --- Metric declarations (module-level singletons) ---

if _available:
    requests_total = Counter(
        "gptme_server_requests_total",
        "Total HTTP requests handled by the gptme server",
        labelnames=["method", "endpoint", "status"],
    )

    request_duration_seconds = Histogram(
        "gptme_server_request_duration_seconds",
        "HTTP request latency in seconds",
        labelnames=["method", "endpoint"],
        buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10),
    )

    conversations_total = Gauge(
        "gptme_server_conversations_total",
        "Total number of persisted conversations",
    )

    messages_total = Gauge(
        "gptme_server_messages_total",
        "Total number of messages across all conversations",
    )

    sse_connections_active = Gauge(
        "gptme_server_sse_connections_active",
        "Number of currently active SSE connections",
    )

    cache_hit_ratio = Gauge(
        "gptme_server_cache_hit_ratio",
        "Cache hit ratio (0.0–1.0) per named cache",
        labelnames=["cache_name"],
    )


# Hit/miss counters for the conversation-list cache (protected by _cache_lock)
_cache_lock = threading.Lock()
_cache_hits = 0
_cache_misses = 0


def sse_connection_open() -> None:
    """Increment the active SSE connections gauge."""
    if not _available:
        return
    sse_connections_active.inc()


def sse_connection_close() -> None:
    """Decrement the active SSE connections gauge."""
    if not _available:
        return
    sse_connections_active.dec()


def record_cache_result(hit: bool, cache_name: str = "conversation_list") -> None:
    """Record a cache hit or miss and update the hit-ratio gauge."""
    if not _available:
        return
    global _cache_hits, _cache_misses
    with _cache_lock:
        if hit:
            _cache_hits += 1
        else:
            _cache_misses += 1
        total = _cache_hits + _cache_misses
        cache_hit_ratio.labels(cache_name=cache_name).set(
            _cache_hits / total if total else 0.0
        )


def update_conversation_metrics(n_conversations: int, n_messages: int) -> None:
    """Update the conversation and message count gauges."""
    if not _available:
        return
    conversations_total.set(n_conversations)
    messages_total.set(n_messages)


def metrics_view() -> "flask.Response":
    """Render current metrics in Prometheus text format."""
    import flask

    if not _available:
        return flask.Response("prometheus_client not installed\n", status=503)
    output = generate_latest()
    return flask.Response(output, mimetype=CONTENT_TYPE_LATEST)


def init_metrics(app: "flask.Flask") -> None:
    """Wire metrics middleware into the Flask app.

    Registers before/after request hooks for request counting and latency
    tracking. No-op when ``prometheus_client`` is not installed.
    """
    import flask

    if not _available:
        logger.debug("prometheus_client not installed; metrics endpoint disabled")
        return

    _METRICS_PATH = "/api/v0/metrics"

    @app.before_request
    def _metrics_before() -> None:
        # Skip timing for the metrics endpoint itself to avoid recursion
        if flask.request.path == _METRICS_PATH:
            return
        flask.g._metrics_start = time.monotonic()

    @app.after_request
    def _metrics_after(response: flask.Response) -> flask.Response:
        if flask.request.path == _METRICS_PATH:
            return response
        start: float | None = getattr(flask.g, "_metrics_start", None)
        if start is None:
            return response
        duration = time.monotonic() - start
        # Use the Flask route rule as the endpoint label to avoid per-resource
        # cardinality explosion (e.g. "/api/v2/conversations/<id>" not the real id)
        endpoint = (
            flask.request.url_rule.rule
            if flask.request.url_rule
            else flask.request.path
        )
        requests_total.labels(
            method=flask.request.method,
            endpoint=endpoint,
            status=str(response.status_code),
        ).inc()
        request_duration_seconds.labels(
            method=flask.request.method,
            endpoint=endpoint,
        ).observe(duration)
        return response

    # Register the metrics endpoint
    app.add_url_rule(_METRICS_PATH, "metrics", metrics_view)

    logger.info("Prometheus metrics enabled at %s", _METRICS_PATH)
