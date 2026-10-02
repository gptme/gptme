"""Tests for telemetry functionality."""

import importlib.util
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _has_telemetry_deps():
    """Check if telemetry dependencies are installed."""
    return (
        importlib.util.find_spec("opentelemetry") is not None
        and importlib.util.find_spec("prometheus_client") is not None
    )


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


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_telemetry_deps(),
    reason="Requires telemetry dependencies (opentelemetry, prometheus_client)",
)
def test_telemetry_startup_preserves_json_stdout():
    """Startup runs before chat selects JSON output; diagnostics belong on stderr."""
    for dependency in (
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.instrumentation.flask",
        "opentelemetry.instrumentation.requests",
        "opentelemetry.instrumentation.openai",
        "opentelemetry.instrumentation.anthropic",
        "opentelemetry.instrumentation.threading",
    ):
        pytest.importorskip(dependency)
    code = """
from unittest.mock import patch
from gptme.init import init_logging
from gptme.message import Message, print_msg, set_output_format
from gptme.util._telemetry import init_telemetry, is_telemetry_enabled, shutdown_telemetry

init_logging(False, stderr=True)
with (
    patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"),
    patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter.export"),
):
    init_telemetry(
        enable_flask_instrumentation=False,
        enable_requests_instrumentation=False,
        enable_openai_instrumentation=False,
        enable_anthropic_instrumentation=False,
        interactive=False,
    )
    assert is_telemetry_enabled()
    set_output_format("json")
    print_msg(Message("assistant", "review complete"))
    shutdown_telemetry()
"""
    # A separate process isolates OpenTelemetry's global providers and threads.
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "GPTME_TELEMETRY_ENABLED": "true",
            "OTLP_ENDPOINT": "http://localhost:4318",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(events) == 1
    assert events[0]["role"] == "assistant"
    assert events[0]["content"] == "review complete"
    assert "Using OTLP" in result.stderr


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_telemetry_deps(),
    reason="Requires telemetry dependencies (opentelemetry, prometheus_client)",
)
def test_telemetry_startup_preserves_json_stdout_after_auto_switch():
    """Auto-switched noninteractive path must not contaminate JSON stdout either.

    When stdin is not a TTY and prompts are supplied, the CLI initially
    configures logging for stdout (interactive mode), then auto-switches to
    noninteractive and re-inits logging to stderr.  Telemetry is initialised
    after that re-init, so its INFO banner must also land on stderr.
    """
    for dependency in (
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.instrumentation.flask",
        "opentelemetry.instrumentation.requests",
        "opentelemetry.instrumentation.openai",
        "opentelemetry.instrumentation.anthropic",
        "opentelemetry.instrumentation.threading",
    ):
        pytest.importorskip(dependency)
    code = """
from unittest.mock import patch
from gptme.init import init_logging
from gptme.message import Message, print_msg, set_output_format
from gptme.util._telemetry import init_telemetry, is_telemetry_enabled, shutdown_telemetry

# Simulate the CLI's interactive startup (stderr=False routes logs to stdout).
init_logging(False, stderr=False)
# Simulate the auto-switch: stdin not a TTY + prompts supplied.
init_logging(False, stderr=True)
with (
    patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"),
    patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter.export"),
):
    init_telemetry(
        enable_flask_instrumentation=False,
        enable_requests_instrumentation=False,
        enable_openai_instrumentation=False,
        enable_anthropic_instrumentation=False,
        interactive=False,
    )
    assert is_telemetry_enabled()
    set_output_format("json")
    print_msg(Message("assistant", "review complete"))
    shutdown_telemetry()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "GPTME_TELEMETRY_ENABLED": "true",
            "OTLP_ENDPOINT": "http://localhost:4318",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(events) == 1
    assert events[0]["role"] == "assistant"
    assert events[0]["content"] == "review complete"
    assert "Using OTLP" in result.stderr


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
    not _has_telemetry_deps(),
    reason="Requires telemetry dependencies (opentelemetry, prometheus_client)",
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


@pytest.mark.skipif(
    not _has_telemetry_deps(),
    reason="Requires telemetry dependencies (opentelemetry, prometheus_client)",
)
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
    assert (
        record.msg == "Telemetry export failed (will suppress further): read timed out"
    )


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


def test_record_llm_request_keeps_values_out_of_labels(monkeypatch):
    """Token counts and cost must be counter values, not metric labels."""
    from unittest.mock import MagicMock

    from gptme import telemetry

    request_counter, cost_counter, token_counter = (
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    objects = {
        "tracer": None,
        "llm_request_counter": request_counter,
        "llm_cost_counter": cost_counter,
        "token_counter": token_counter,
    }
    monkeypatch.setattr(telemetry, "is_telemetry_enabled", lambda: True)
    monkeypatch.setattr(telemetry, "get_telemetry_objects", lambda: objects)
    monkeypatch.setattr(telemetry, "_calculate_llm_cost", lambda **_: 0.0123)

    telemetry.record_llm_request(
        "anthropic",
        "claude-haiku-4-5",
        input_tokens=1000,
        output_tokens=100,
        total_tokens=1100,
    )

    request_counter.add.assert_called_once_with(
        1, {"provider": "anthropic", "model": "claude-haiku-4-5", "success": "true"}
    )
    cost_counter.add.assert_called_once_with(
        0.0123, {"provider": "anthropic", "model": "claude-haiku-4-5"}
    )
    token_counter.add.assert_any_call(1000, {"token_type": "input"})


@pytest.mark.slow
@pytest.mark.skipif(
    not _has_telemetry_deps(),
    reason="Requires telemetry dependencies (opentelemetry, prometheus_client)",
)
def test_llm_cost_exported_by_real_meter():
    """init_telemetry registers gptme_llm_cost_usd and a real meter exports it.

    Catches what the mock-counter test cannot: a missing registration, a wrong
    metric name, or a lost fractional value.
    """
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.metric_exporter")
    code = """
import json
from unittest.mock import patch
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

# Install our provider first; init_telemetry's later set_meter_provider is
# ignored by OpenTelemetry, so its instruments land on this in-memory reader.
reader = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))

from gptme import telemetry
from gptme.util._telemetry import init_telemetry, shutdown_telemetry

with (
    patch("opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"),
    patch("opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter.export"),
    patch.object(telemetry, "_calculate_llm_cost", lambda **_: 0.0123),
):
    init_telemetry(
        enable_flask_instrumentation=False,
        enable_requests_instrumentation=False,
        enable_openai_instrumentation=False,
        enable_anthropic_instrumentation=False,
        interactive=False,
    )
    telemetry.record_llm_request(
        "anthropic", "claude-haiku-4-5", input_tokens=1000, output_tokens=100
    )
    points = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                points[m.name] = [
                    (dict(p.attributes), p.value) for p in m.data.data_points
                ]
    print(json.dumps(points))
    shutdown_telemetry()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "GPTME_TELEMETRY_ENABLED": "true",
            "OTLP_ENDPOINT": "http://localhost:4318",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    points = json.loads(result.stdout.strip().splitlines()[-1])
    assert points["gptme_llm_cost_usd"] == [
        [{"provider": "anthropic", "model": "claude-haiku-4-5"}, 0.0123]
    ]
    # the request counter carries no token/cost labels
    [[labels, value]] = points["gptme_llm_requests"]
    assert labels == {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "success": "true",
    }
    assert value == 1
