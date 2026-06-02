"""Tests for telemetry functionality."""

import logging
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


def test_calculate_llm_cost_resolves_anthropic_short_alias():
    """Anthropic short aliases should use Anthropic pricing metadata."""
    from gptme.telemetry import _calculate_llm_cost

    usage = {
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_creation_tokens": 2000,
        "cache_read_tokens": 3000,
    }

    alias_cost = _calculate_llm_cost(
        provider="anthropic",
        model="claude-haiku-4-5",
        **usage,
    )
    dated_cost = _calculate_llm_cost(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        **usage,
    )

    assert alias_cost > 0
    assert alias_cost == pytest.approx(dated_cost)


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


def test_connection_error_filter_truncates_read_timeout_traceback():
    """Timeout-style OTLP export errors should be reduced to one-line noise."""
    from gptme.util._telemetry import TelemetryConnectionErrorFilter

    class ReadTimeout(Exception):
        pass

    record = logging.LogRecord(
        name="opentelemetry.sdk._shared_internal",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Exception while exporting Span.",
        args=("stale-format-arg",),
        exc_info=(ReadTimeout, ReadTimeout("read timed out"), None),
    )

    filter_ = TelemetryConnectionErrorFilter(cooldown_seconds=300.0)

    assert filter_.filter(record) is True
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.args == ()
    assert record.msg == "Telemetry export failed: read timed out"


def test_connection_error_filter_debounces_repeated_timeouts():
    """Repeated OTLP timeout errors should be suppressed inside the cooldown."""
    from gptme.util._telemetry import TelemetryConnectionErrorFilter

    class ReadTimeout(Exception):
        pass

    filter_ = TelemetryConnectionErrorFilter(cooldown_seconds=300.0)
    first = logging.LogRecord(
        name="opentelemetry.sdk._shared_internal",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Exception while exporting Span.",
        args=("stale-format-arg",),
        exc_info=(ReadTimeout, ReadTimeout("read timed out"), None),
    )
    second = logging.LogRecord(
        name="opentelemetry.sdk._shared_internal",
        level=logging.ERROR,
        pathname=__file__,
        lineno=2,
        msg="Exception while exporting Span.",
        args=("stale-format-arg",),
        exc_info=(ReadTimeout, ReadTimeout("read timed out"), None),
    )

    assert filter_.filter(first) is True
    assert filter_.filter(second) is False


def test_record_hook_call_records_span():
    """Hook spans should preserve timing and attributes for tracing."""
    from gptme.telemetry import record_hook_call

    class FakeSpan:
        def __init__(self, name, start_time=None):
            self.name = name
            self.start_time = start_time
            self.attributes = {}
            self.events = []
            self.end_time = None

        def set_attribute(self, key, value):
            self.attributes[key] = value

        def add_event(self, name, attributes=None):
            self.events.append((name, attributes))

        def end(self, end_time=None):
            self.end_time = end_time

    class FakeTracer:
        def __init__(self):
            self.spans = []

        def start_span(self, name, **kwargs):
            span = FakeSpan(name, kwargs.get("start_time"))
            self.spans.append(span)
            return span

    tracer = FakeTracer()

    with (
        patch("gptme.telemetry.is_telemetry_enabled", return_value=True),
        patch("gptme.telemetry.get_telemetry_objects", return_value={"tracer": tracer}),
        patch("gptme.telemetry.enrich_span_with_context"),
    ):
        record_hook_call(
            hook_name="hook-a",
            hook_type="step.pre",
            async_mode=False,
            duration=1.25,
            success=False,
            error_type="ValueError",
            error_message="bad hook",
            start_time_ns=123,
        )

    assert len(tracer.spans) == 1
    span = tracer.spans[0]
    assert span.name == "hook.step.pre.hook-a"
    assert span.start_time == 123
    assert span.end_time == 1_250_000_123
    assert span.attributes["hook.name"] == "hook-a"
    assert span.attributes["hook.type"] == "step.pre"
    assert span.attributes["hook.async_mode"] is False
    assert span.attributes["hook.success"] is False
    assert span.attributes["hook.duration_seconds"] == 1.25
    assert span.attributes["hook.error.type"] == "ValueError"
    assert span.attributes["hook.error.message"] == "bad hook"
    assert span.events == [
        (
            "hook_failed",
            {
                "hook": "hook-a",
                "hook_type": "step.pre",
                "error_type": "ValueError",
            },
        )
    ]


def test_otlp_timeout_seconds_default_when_unset(monkeypatch):
    """Unset OTEL_EXPORTER_OTLP_TIMEOUT falls back to the provided default."""
    from gptme.util._telemetry import _otlp_timeout_seconds

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TIMEOUT", raising=False)
    assert _otlp_timeout_seconds(default=10.0) == 10.0
    assert _otlp_timeout_seconds(default=5.0) == 5.0


def test_otlp_timeout_seconds_honors_env_milliseconds(monkeypatch):
    """OTEL_EXPORTER_OTLP_TIMEOUT is read in milliseconds and converted to seconds."""
    from gptme.util._telemetry import _otlp_timeout_seconds

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1000")
    assert _otlp_timeout_seconds(default=10.0) == 1.0
    # Same env value applies regardless of the default (fast-fail override).
    assert _otlp_timeout_seconds(default=5.0) == 1.0


def test_otlp_timeout_seconds_invalid_falls_back(monkeypatch):
    """A non-integer env value falls back to the default instead of raising."""
    from gptme.util._telemetry import _otlp_timeout_seconds

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "not-a-number")
    assert _otlp_timeout_seconds(default=10.0) == 10.0


def test_otlp_timeout_seconds_zero_falls_back(monkeypatch):
    """A zero timeout is spec-invalid (must be positive) and falls back to default."""
    from gptme.util._telemetry import _otlp_timeout_seconds

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "0")
    assert _otlp_timeout_seconds(default=10.0) == 10.0


def test_otlp_timeout_seconds_negative_falls_back(monkeypatch):
    """A negative timeout is spec-invalid and falls back to default with a warning."""
    from gptme.util._telemetry import _otlp_timeout_seconds

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "-1000")
    assert _otlp_timeout_seconds(default=10.0) == 10.0


def test_otlp_timeout_seconds_overflow_falls_back(monkeypatch):
    """An 'inf' value triggers OverflowError on int() and falls back to the default."""
    from gptme.util._telemetry import _otlp_timeout_seconds

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "inf")
    assert _otlp_timeout_seconds(default=10.0) == 10.0
