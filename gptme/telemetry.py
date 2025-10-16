"""
Telemetry integration for gptme performance monitoring.

This module provides tracing and metrics collection to measure:
- Parsing speeds
- Server tokens/second
- Tool execution times
- LLM response times

Heavy OpenTelemetry imports are lazy-loaded from _telemetry module.
"""

import functools
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from .llm.models import get_model
from .util._telemetry import get_telemetry_objects
from .util._telemetry import init_telemetry as _init
from .util._telemetry import is_telemetry_enabled as _is_enabled
from .util._telemetry import shutdown_telemetry as _shutdown

logger = logging.getLogger(__name__)

# Type variable for generic function decoration
F = TypeVar("F", bound=Callable[..., Any])


def is_telemetry_enabled() -> bool:
    """Check if telemetry is enabled."""
    return _is_enabled()


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
    try:
        _init(
            service_name=service_name,
            enable_flask_instrumentation=enable_flask_instrumentation,
            enable_requests_instrumentation=enable_requests_instrumentation,
            enable_openai_instrumentation=enable_openai_instrumentation,
            enable_anthropic_instrumentation=enable_anthropic_instrumentation,
            agent_name=agent_name,
            interactive=interactive,
        )
    except ImportError:
        logger.warning(
            "Telemetry dependencies not available. Install with: pip install gptme[telemetry]"
        )


def shutdown_telemetry() -> None:
    """Shutdown telemetry providers."""
    try:
        _shutdown()
    except ImportError:
        pass


def trace_function(
    name: str | None = None, attributes: dict[str, Any] | None = None
) -> Callable[[F], F]:
    """Decorator to trace function execution."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not is_telemetry_enabled():
                return func(*args, **kwargs)

            telemetry_objects = get_telemetry_objects()
            tracer = telemetry_objects["tracer"]

            if tracer is None:
                return func(*args, **kwargs)

            span_name = name or f"{func.__module__}.{func.__name__}"

            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)

                # Add function info
                span.set_attribute("function.name", func.__name__)
                span.set_attribute("function.module", func.__module__)

                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("function.result.success", True)
                    return result
                except Exception as e:
                    span.set_attribute("function.result.success", False)
                    span.set_attribute("function.error.type", type(e).__name__)
                    span.set_attribute("function.error.message", str(e))
                    raise

        return wrapper  # type: ignore

    return decorator


def record_tokens(count: int, token_type: str = "total") -> None:
    """Record token count metric."""
    if not is_telemetry_enabled():
        return

    telemetry_objects = get_telemetry_objects()
    token_counter = telemetry_objects["token_counter"]

    if token_counter is not None:
        token_counter.add(count, {"token_type": token_type})


def record_request_duration(
    duration: float, endpoint: str, method: str = "GET"
) -> None:
    """Record request duration metric."""
    if not is_telemetry_enabled():
        return

    telemetry_objects = get_telemetry_objects()
    request_histogram = telemetry_objects["request_histogram"]

    if request_histogram is not None:
        request_histogram.record(duration, {"endpoint": endpoint, "method": method})


def record_tool_call(
    tool_name: str,
    duration: float | None = None,
    success: bool = True,
    error_type: str | None = None,
    error_message: str | None = None,
    tool_format: str | None = None,
) -> None:
    """Record tool call metrics."""
    if not is_telemetry_enabled():
        return

    telemetry_objects = get_telemetry_objects()
    tool_counter = telemetry_objects["tool_counter"]
    tool_duration_histogram = telemetry_objects["tool_duration_histogram"]

    if tool_counter is None:
        return

    attributes = {"tool_name": tool_name, "success": str(success).lower()}

    if tool_format:
        attributes["tool_format"] = tool_format
    if error_type:
        attributes["error_type"] = error_type
    if error_message:
        # Truncate long error messages
        attributes["error_message"] = error_message[:200]

    tool_counter.add(1, attributes)

    if duration is not None and tool_duration_histogram is not None:
        tool_duration_histogram.record(duration, attributes)


def record_conversation_change(delta: int) -> None:
    """Record change in active conversations (+1 for new, -1 for ended)."""
    if not is_telemetry_enabled():
        return

    telemetry_objects = get_telemetry_objects()
    active_conversations_gauge = telemetry_objects["active_conversations_gauge"]

    if active_conversations_gauge is not None:
        active_conversations_gauge.add(delta)


def _calculate_llm_cost(
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    cache_read_tokens: int | None = None,
) -> float:
    """Calculate the cost of an LLM request."""
    meta = get_model(f"{model}")
    if not (meta and input_tokens and output_tokens):
        return 0.0

    price_in = (meta.price_input or 0.0) / 1e6
    price_out = (meta.price_output or 0.0) / 1e6
    cost = input_tokens * price_in + output_tokens * price_out

    # Cache pricing per provider
    caching_cost = 0.0
    if provider == "anthropic":
        # anthropic charges 1.25x for cache writes + 0.1x for cache reads
        price_cache_read = 0.1 * price_out
        price_cache_write = 1.25 * price_in
        cost_cache_read = price_cache_read * (cache_read_tokens or 0)
        cost_cache_write = price_cache_write * (cache_creation_tokens or 0)
        caching_cost = cost_cache_read + cost_cache_write
    elif provider == "openai":
        # openai charges 0.5x for cache reads
        price_cache_read = 0.5 * price_out
        caching_cost = price_cache_read * (cache_read_tokens or 0)

    return cost + caching_cost


def record_llm_request(
    provider: str,
    model: str,
    success: bool = True,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """Record LLM API request metrics with optional token usage."""
    logger.debug(
        f"Recording LLM request: provider={provider}, model={model}, success={success}"
    )
    total_in = (input_tokens or 0) + (cache_read_tokens or 0)
    total_out = (output_tokens or 0) + (cache_creation_tokens or 0)
    logger.debug(
        f"tokens in:  {input_tokens}"
        + (
            f" + {cache_read_tokens} cached ({100*(cache_read_tokens or 0)/(total_in):.1f}%)"
            if cache_read_tokens
            else ""
        )
    )
    logger.debug(
        f"tokens out: {output_tokens}"
        + (f" + {cache_creation_tokens} cache created" if cache_creation_tokens else "")
    )

    if total_tokens:
        # check that total_tokens matches sum of parts
        if not total_tokens == total_in + total_out:
            logger.warning(
                f"Total tokens {total_tokens} does not match sum of parts {total_in + total_out}, this is an implementation issue."
            )

    cost = _calculate_llm_cost(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
    )
    logger.debug(f"LLM request cost: ${cost:.6f}")

    if not is_telemetry_enabled():
        return

    telemetry_objects = get_telemetry_objects()
    llm_request_counter = telemetry_objects["llm_request_counter"]

    if llm_request_counter is None:
        return

    attributes = {
        "provider": provider,
        "model": model,
        "success": str(success).lower(),
    }

    # Add token counts as attributes if provided
    if input_tokens is not None:
        attributes["input_tokens"] = str(input_tokens)
    if output_tokens is not None:
        attributes["output_tokens"] = str(output_tokens)
    if cache_creation_tokens is not None:
        attributes["cache_creation_tokens"] = str(cache_creation_tokens)
    if cache_read_tokens is not None:
        attributes["cache_read_tokens"] = str(cache_read_tokens)
    if total_tokens is not None:
        attributes["total_tokens"] = str(total_tokens)
    if cost > 0:
        attributes["cost"] = f"{cost:.6f}"

    llm_request_counter.add(1, attributes)

    # Also record individual token metrics for existing dashboards/queries
    if input_tokens:
        record_tokens(input_tokens, "input")
    if output_tokens:
        record_tokens(output_tokens, "output")
    if cache_creation_tokens:
        record_tokens(cache_creation_tokens, "cache_creation")
    if cache_read_tokens:
        record_tokens(cache_read_tokens, "cache_read")
    if total_tokens:
        record_tokens(total_tokens, "total")


def measure_tokens_per_second(func: F) -> F:
    """Decorator to measure tokens per second for LLM operations."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not is_telemetry_enabled():
            return func(*args, **kwargs)

        telemetry_objects = get_telemetry_objects()
        tracer = telemetry_objects["tracer"]

        if tracer is None:
            return func(*args, **kwargs)

        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()

        # Try to extract token count from result
        # This is a heuristic and may need adjustment based on actual return types
        token_count = 0
        if hasattr(result, "usage") and hasattr(result.usage, "total_tokens"):
            token_count = result.usage.total_tokens
        elif isinstance(result, dict) and "usage" in result:
            token_count = result["usage"].get("total_tokens", 0)

        if token_count > 0:
            duration = end_time - start_time
            tokens_per_second = token_count / duration if duration > 0 else 0

            with tracer.start_as_current_span("llm_tokens_per_second") as span:
                span.set_attribute("tokens.count", token_count)
                span.set_attribute("tokens.duration_seconds", duration)
                span.set_attribute("tokens.per_second", tokens_per_second)

            record_tokens(token_count, "llm_response")

        return result

    return wrapper  # type: ignore
