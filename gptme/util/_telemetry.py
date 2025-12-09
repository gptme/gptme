"""
OpenTelemetry implementation details for gptme performance monitoring.

This module contains the heavy OpenTelemetry imports and initialization logic,
separated from the main telemetry module to avoid importing large dependencies
unless explicitly needed.
"""

import logging
import os
import socket
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

# Context variables for conversation tracking
# These propagate across async/threaded operations
_conversation_id: ContextVar[str | None] = ContextVar("conversation_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)


class TelemetryConnectionErrorFilter(logging.Filter):
    """Filter to truncate verbose connection error stack traces from OpenTelemetry."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Truncate connection error messages to a single line.

        Note: Returns True to allow the modified record to pass through.
        We want to show the error but without the verbose stack trace.
        """
        # Check if this is a connection error from OpenTelemetry exporters
        if (
            record.name.startswith("opentelemetry.")
            and record.levelno == logging.ERROR
            and hasattr(record, "exc_info")
            and record.exc_info
        ):
            exc_type, exc_value, _ = record.exc_info
            # Check if it's a connection-related error
            if exc_type and (
                "ConnectionError" in exc_type.__name__
                or "NewConnectionError" in exc_type.__name__
                or "MaxRetryError" in exc_type.__name__
            ):
                # Replace the full stack trace with a simple message
                record.exc_info = None
                record.exc_text = None
                record.msg = f"Telemetry connection failed: {exc_value}"
        return True


class NotGivenAttributeFilter(logging.Filter):
    """Filter to suppress warnings about NotGiven type in telemetry attributes.

    The opentelemetry-instrumentation-anthropic and opentelemetry-instrumentation-openai
    libraries capture API parameters including those set to NOT_GIVEN sentinel values.
    OTEL's attribute validation warns about these since NotGiven is not a valid
    attribute type. The data is still exported correctly, so we suppress the warning.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Suppress NotGiven attribute type warnings.

        Returns False to drop the record, True to allow it through.
        """
        # Check if this is a NotGiven type warning
        if (
            record.name.startswith("opentelemetry.")
            and record.levelno == logging.WARNING
            and "NotGiven" in record.getMessage()
            and "Invalid type" in record.getMessage()
        ):
            # Suppress this specific warning - it's noise from sentinel values
            return False
        return True


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
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,  # fmt: skip
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,  # fmt: skip
    )
    from opentelemetry.instrumentation.anthropic import (
        AnthropicInstrumentor,  # fmt: skip
    )
    from opentelemetry.instrumentation.flask import FlaskInstrumentor  # fmt: skip
    from opentelemetry.instrumentation.openai import OpenAIInstrumentor  # fmt: skip
    from opentelemetry.instrumentation.requests import RequestsInstrumentor  # fmt: skip
    from opentelemetry.instrumentation.threading import (
        ThreadingInstrumentor,  # fmt: skip
    )
    from opentelemetry.sdk.metrics import MeterProvider  # fmt: skip
    from opentelemetry.sdk.metrics.export import (
        PeriodicExportingMetricReader,  # fmt: skip
    )
    from opentelemetry.sdk.metrics.view import (  # fmt: skip
        ExplicitBucketHistogramAggregation,
        View,
    )
    from opentelemetry.sdk.resources import Resource  # fmt: skip
    from opentelemetry.sdk.trace import TracerProvider  # fmt: skip
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # fmt: skip

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


def set_conversation_context(
    conversation_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Set the current conversation context for tracing.

    This context propagates to all spans created in the current thread/async context,
    enabling filtering by conversation in Jaeger.

    Args:
        conversation_id: Unique identifier for the conversation (e.g., log name)
        session_id: Unique identifier for the session (e.g., UUID)
    """
    if conversation_id is not None:
        _conversation_id.set(conversation_id)
    if session_id is not None:
        _session_id.set(session_id)


def get_conversation_context() -> tuple[str | None, str | None]:
    """Get the current conversation context.

    Returns:
        Tuple of (conversation_id, session_id)
    """
    return _conversation_id.get(), _session_id.get()


def clear_conversation_context() -> None:
    """Clear the conversation context."""
    _conversation_id.set(None)
    _session_id.set(None)


def enrich_span_with_context(span: "Span") -> None:
    """Add conversation context to a span.

    Args:
        span: The OpenTelemetry span to enrich
    """
    conversation_id, session_id = get_conversation_context()
    if conversation_id:
        span.set_attribute("conversation.id", conversation_id)
    if session_id:
        span.set_attribute("session.id", session_id)


def enrich_span_with_llm_metrics(
    span: "Span",
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cost: float | None = None,
) -> None:
    """Add LLM metrics to a span for analysis in Jaeger.

    Args:
        span: The OpenTelemetry span to enrich
        input_tokens: Number of input tokens (excluding cache)
        output_tokens: Number of output tokens
        cache_creation_tokens: Tokens written to cache
        cache_read_tokens: Tokens read from cache
        cost: Calculated cost in USD
    """
    if input_tokens is not None:
        span.set_attribute("llm.tokens.input", input_tokens)
    if output_tokens is not None:
        span.set_attribute("llm.tokens.output", output_tokens)
    if cache_creation_tokens is not None:
        span.set_attribute("llm.cache.creation_tokens", cache_creation_tokens)
    if cache_read_tokens is not None:
        span.set_attribute("llm.cache.read_tokens", cache_read_tokens)

    # Calculate and add cache hit rate
    if cache_read_tokens is not None and cache_creation_tokens is not None:
        total_cacheable = (
            (input_tokens or 0) + cache_read_tokens + cache_creation_tokens
        )
        if total_cacheable > 0:
            hit_rate = cache_read_tokens / total_cacheable
            span.set_attribute("llm.cache.hit_rate", hit_rate)

    if cost is not None:
        span.set_attribute("llm.cost.usd", cost)


def init_telemetry(
    service_name: str = "gptme",
    enable_flask_instrumentation: bool = True,
    enable_requests_instrumentation: bool = True,
    enable_openai_instrumentation: bool = True,
    enable_anthropic_instrumentation: bool = True,
    agent_name: str | None = None,
    interactive: bool | None = None,
) -> None:
    """Initialize OpenTelemetry tracing and metrics.

    Args:
        service_name: Name of the service for telemetry
        enable_flask_instrumentation: Whether to auto-instrument Flask
        enable_requests_instrumentation: Whether to auto-instrument requests library
        enable_openai_instrumentation: Whether to auto-instrument OpenAI
        enable_anthropic_instrumentation: Whether to auto-instrument Anthropic
        agent_name: Name of the agent (from gptme.toml [agent].name)
        interactive: Whether running in interactive mode (None = unknown, False = autonomous)
    """
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
        # Initialize tracing with proper service name and additional metadata
        resource_attrs = {"service.name": service_name}

        # Add hostname (standard OpenTelemetry attribute)

        try:
            hostname = socket.gethostname()
            resource_attrs["host.name"] = hostname
            logger.debug(f"Adding host.name to resource: {hostname}")
        except Exception as e:
            logger.warning(f"Failed to get hostname: {e}")

        # Add agent name if provided
        if agent_name:
            resource_attrs["agent.name"] = agent_name
            logger.debug(f"Adding agent.name to resource: {agent_name}")

        # Add interactive mode if known
        if interactive is not None:
            resource_attrs["agent.interactive"] = str(interactive).lower()
            if not interactive:
                logger.debug("Running in autonomous mode")

        resource = Resource.create(resource_attrs)
        trace.set_tracer_provider(TracerProvider(resource=resource))
        _tracer = trace.get_tracer(service_name)

        # Set up OTLP exporter if endpoint provided (for Jaeger or other OTLP-compatible backends)
        # Using HTTP instead of gRPC for better compatibility
        # OTLP uses port 4318 for HTTP, 4317 for gRPC
        # HTTP exporters need the full path including /v1/traces
        otlp_endpoint = os.getenv("OTLP_ENDPOINT") or "http://localhost:4318"

        # Ensure endpoint ends with /v1/traces for the trace exporter
        trace_endpoint = otlp_endpoint
        if not trace_endpoint.endswith("/v1/traces"):
            trace_endpoint = trace_endpoint.rstrip("/") + "/v1/traces"

        otlp_exporter = OTLPSpanExporter(
            endpoint=trace_endpoint,
            timeout=10,  # 10 second timeout for exports
        )
        span_processor = BatchSpanProcessor(
            otlp_exporter,
            max_export_batch_size=512,
            schedule_delay_millis=5000,  # Export every 5 seconds
        )
        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, "add_span_processor"):
            tracer_provider.add_span_processor(span_processor)  # type: ignore

        # Use OTLP for metrics (same endpoint as traces)
        try:
            # Ensure endpoint ends with /v1/metrics for the metric exporter
            metric_endpoint = otlp_endpoint
            if not metric_endpoint.endswith("/v1/metrics"):
                metric_endpoint = metric_endpoint.rstrip("/") + "/v1/metrics"

            otlp_metric_exporter = OTLPMetricExporter(
                endpoint=metric_endpoint,
                timeout=10,  # 10 second timeout for exports
            )
            metric_reader = PeriodicExportingMetricReader(
                otlp_metric_exporter,
                export_interval_millis=10000,  # Export every 10 seconds (faster feedback)
                export_timeout_millis=10000,  # 10 second timeout for export
            )
            # Configure custom histogram buckets for different metrics
            # Tool durations: 0.1s to 5min (most tools: 0.1-30s)
            tool_duration_view = View(
                instrument_name="gptme_tool_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0]
                ),
            )

            # HTTP request durations: 10ms to 30s (most requests: 50ms-5s)
            request_duration_view = View(
                instrument_name="gptme_request_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=[0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
                ),
            )

            metrics.set_meter_provider(
                MeterProvider(
                    resource=resource,
                    metric_readers=[metric_reader],
                    views=[tool_duration_view, request_duration_view],
                )
            )
        except ImportError as e:
            logger.warning(f"OTLP metric exporter not available: {e}")
            # Initialize without metrics if OTLP not available
            # Still configure views for consistency
            tool_duration_view = View(
                instrument_name="gptme_tool_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0]
                ),
            )

            request_duration_view = View(
                instrument_name="gptme_request_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=[0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
                ),
            )

            metrics.set_meter_provider(
                MeterProvider(views=[tool_duration_view, request_duration_view])
            )

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

        # Always enable threading instrumentation for context propagation
        # This is critical for server mode where threads handle requests
        ThreadingInstrumentor().instrument()

        _telemetry_enabled = True

        # Apply filters to OpenTelemetry loggers
        connection_filter = TelemetryConnectionErrorFilter()
        notgiven_filter = NotGivenAttributeFilter()

        # Connection error filter truncates verbose stack traces to single lines
        for otel_logger_name in [
            "opentelemetry.exporter.otlp.proto.http",
            "opentelemetry.sdk._shared_internal",
            "opentelemetry.sdk.metrics._internal.export",
        ]:
            otel_logger = logging.getLogger(otel_logger_name)
            otel_logger.addFilter(connection_filter)

        # NotGiven filter suppresses warnings about sentinel values in attributes
        # This affects the attribute validation logger
        otel_attributes_logger = logging.getLogger("opentelemetry.attributes")
        otel_attributes_logger.addFilter(notgiven_filter)

        # Import console for user-visible messages
        from . import console  # fmt: skip

        # Log to console so users know telemetry is active
        console.log(f"Using OTLP to send metrics and traces to {otlp_endpoint}")

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

        # Force flush and shutdown meter provider
        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "force_flush"):
            logger.debug("Flushing pending metrics...")
            meter_provider.force_flush(timeout_millis=5000)  # 5 second timeout
        if hasattr(meter_provider, "shutdown"):
            logger.debug("Shutting down meter provider...")
            meter_provider.shutdown()

        _telemetry_enabled = False
        logger.info("Telemetry shutdown successfully")

    except Exception as e:
        logger.error(f"Failed to shutdown telemetry: {e}")
