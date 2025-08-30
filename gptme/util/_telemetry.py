"""
OpenTelemetry implementation details for gptme performance monitoring.

This module contains the heavy OpenTelemetry imports and initialization logic,
separated from the main telemetry module to avoid importing large dependencies
unless explicitly needed.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Global variables to track telemetry state
_telemetry_enabled = False
_tracer = None
_meter = None
_token_counter = None
_request_histogram = None
_tool_counter = None
_tool_duration_histogram = None
_active_conversations_gauge = None
_llm_request_counter = None

TELEMETRY_AVAILABLE = False
TELEMETRY_IMPORT_ERROR = None

try:
    from opentelemetry import metrics, trace  # fmt: skip
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,  # fmt: skip
    )
    from opentelemetry.exporter.prometheus import PrometheusMetricReader  # fmt: skip
    from opentelemetry.instrumentation.anthropic import (
        AnthropicInstrumentor,  # fmt: skip
    )
    from opentelemetry.instrumentation.flask import FlaskInstrumentor  # fmt: skip
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor  # fmt: skip
    from opentelemetry.instrumentation.requests import RequestsInstrumentor  # fmt: skip
    from opentelemetry.sdk.metrics import MeterProvider  # fmt: skip
    from opentelemetry.sdk.resources import Resource  # fmt: skip
    from opentelemetry.sdk.trace import TracerProvider  # fmt: skip
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # fmt: skip
    from prometheus_client import start_http_server  # fmt: skip

    TELEMETRY_AVAILABLE = True
except ImportError as e:
    TELEMETRY_AVAILABLE = False
    TELEMETRY_IMPORT_ERROR = str(e)


def is_telemetry_enabled() -> bool:
    """Check if telemetry is enabled."""
    return _telemetry_enabled and TELEMETRY_AVAILABLE


def get_telemetry_objects():
    """Get telemetry objects (tracer, meter, counters, etc.)."""
    return {
        "tracer": _tracer,
        "meter": _meter,
        "token_counter": _token_counter,
        "request_histogram": _request_histogram,
        "tool_counter": _tool_counter,
        "tool_duration_histogram": _tool_duration_histogram,
        "active_conversations_gauge": _active_conversations_gauge,
        "llm_request_counter": _llm_request_counter,
    }


def init_telemetry(
    service_name: str = "gptme",
    enable_flask_instrumentation: bool = True,
    enable_requests_instrumentation: bool = True,
    enable_openai_instrumentation: bool = True,
    enable_anthropic_instrumentation: bool = True,
    prometheus_port: int = 8000,
) -> None:
    """Initialize OpenTelemetry tracing and metrics."""
    global _telemetry_enabled, _tracer, _meter, _token_counter, _request_histogram
    global \
        _tool_counter, \
        _tool_duration_histogram, \
        _active_conversations_gauge, \
        _llm_request_counter

    # Check if telemetry is enabled via environment variable
    if os.getenv("GPTME_TELEMETRY_ENABLED", "").lower() not in ("true", "1", "yes"):
        logger.debug(
            "Telemetry not enabled. Set GPTME_TELEMETRY_ENABLED=true to enable."
        )
        return

    if not TELEMETRY_AVAILABLE:
        error_msg = "OpenTelemetry dependencies not available. Install with: pip install gptme[telemetry]"
        if TELEMETRY_IMPORT_ERROR:
            error_msg += f" (Import error: {TELEMETRY_IMPORT_ERROR})"
        logger.warning(error_msg)
        return

    try:
        # Initialize tracing with proper service name
        resource = Resource.create({"service.name": service_name})
        trace.set_tracer_provider(TracerProvider(resource=resource))
        _tracer = trace.get_tracer(service_name)

        # Set up OTLP exporter if endpoint provided (for Jaeger or other OTLP-compatible backends)
        # OTLP uses different default ports: 4317 for gRPC, 4318 for HTTP
        otlp_endpoint = os.getenv("OTLP_ENDPOINT") or "http://localhost:4317"
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        span_processor = BatchSpanProcessor(otlp_exporter)
        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, "add_span_processor"):
            tracer_provider.add_span_processor(span_processor)  # type: ignore

        # Initialize metrics with Prometheus reader
        prometheus_port = int(os.getenv("PROMETHEUS_PORT", prometheus_port))
        prometheus_addr = os.getenv("PROMETHEUS_ADDR", "localhost")

        # Start Prometheus HTTP server to expose metrics
        start_http_server(port=prometheus_port, addr=prometheus_addr)

        # Initialize PrometheusMetricReader which pulls metrics from the SDK
        # on-demand to respond to scrape requests
        prometheus_reader = PrometheusMetricReader()
        metrics.set_meter_provider(MeterProvider(metric_readers=[prometheus_reader]))
        _meter = metrics.get_meter(service_name)

        # Create metrics
        _token_counter = _meter.create_counter(
            name="gptme_tokens_processed",
            description="Number of tokens processed",
            unit="tokens",
        )

        _request_histogram = _meter.create_histogram(
            name="gptme_request_duration_seconds",
            description="Request duration in seconds",
            unit="seconds",
        )

        _tool_counter = _meter.create_counter(
            name="gptme_tool_calls",
            description="Number of tool calls made",
            unit="calls",
        )

        _tool_duration_histogram = _meter.create_histogram(
            name="gptme_tool_duration_seconds",
            description="Tool execution duration in seconds",
            unit="seconds",
        )

        _active_conversations_gauge = _meter.create_up_down_counter(
            name="gptme_active_conversations",
            description="Number of active conversations",
            unit="conversations",
        )

        _llm_request_counter = _meter.create_counter(
            name="gptme_llm_requests",
            description="Number of LLM API requests made",
            unit="requests",
        )

        # Auto-instrument Flask and requests if enabled
        if enable_flask_instrumentation:
            FlaskInstrumentor().instrument()

        if enable_requests_instrumentation:
            RequestsInstrumentor().instrument()

        if enable_openai_instrumentation:
            OpenAIInstrumentor().instrument()

        if enable_anthropic_instrumentation:
            AnthropicInstrumentor().instrument()

        _telemetry_enabled = True

        # Import console for user-visible messages
        from . import console  # fmt: skip

        # Log to console so users know telemetry is active
        console.log("📊 Telemetry enabled - performance metrics will be collected")
        console.log(f"🔍 Traces will be sent via OTLP to {otlp_endpoint}")
        console.log(
            f"📈 Prometheus metrics available at http://{prometheus_addr}:{prometheus_port}/metrics"
        )

    except Exception as e:
        logger.error(f"Failed to initialize telemetry: {e}")


def shutdown_telemetry() -> None:
    """Shutdown telemetry providers."""
    global _telemetry_enabled

    if not _telemetry_enabled:
        return

    try:
        # Force flush any pending spans before shutdown
        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, "force_flush"):
            logger.debug("Flushing pending traces...")
            tracer_provider.force_flush(timeout_millis=5000)  # 5 second timeout

        # Shutdown tracer provider
        if hasattr(tracer_provider, "shutdown"):
            tracer_provider.shutdown()

        # Shutdown meter provider
        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "shutdown"):
            meter_provider.shutdown()

        _telemetry_enabled = False
        logger.info("Telemetry shutdown successfully")

    except Exception as e:
        logger.error(f"Failed to shutdown telemetry: {e}")
