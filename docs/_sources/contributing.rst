Contributing
============

We welcome contributions to the project. Here is some information to get you started.

.. note::
    This document is a work in progress. PRs are welcome.

Install
-------

.. code-block:: bash

   # checkout the code and navigate to the root of the project
   git clone https://github.com/gptme/gptme.git
   cd gptme

   # install poetry (if not installed)
   pipx install poetry

   # activate the virtualenv
   poetry shell

   # build the project
   make build

You can now start ``gptme`` from your development environment using the regular commands.

You can also install it in editable mode with ``pipx`` using ``pipx install -e .`` which will let you use your development version of gptme regardless of venv.

Tests
-----

Run tests with ``make test``.

Some tests make LLM calls, which might take a while and so are not run by default. You can run them with ``make test SLOW=true``.

There are also some integration tests in ``./tests/test-integration.sh`` which are used to manually test more complex tasks.

There is also the :doc:`evals`.

Telemetry
---------

gptme includes optional OpenTelemetry integration for performance monitoring and debugging. This is useful for development to understand performance characteristics and identify bottlenecks.

Setup
~~~~~

To enable telemetry during development:

1. Install telemetry dependencies:

   .. code-block:: bash

      poetry install -E telemetry

2. Run Jaeger for trace visualization:

   .. code-block:: bash

      docker run --rm --name jaeger \
                -p 16686:16686 \
                -p 4317:4317 \
                -p 4318:4318 \
                -p 5778:5778 \
                -p 9411:9411 \
                cr.jaegertracing.io/jaegertracing/jaeger:latest

3. Run Prometheus for metrics collection:

   .. code-block:: bash

      docker run --rm --name prometheus \
                -p 9090:9090 \
                -v $(pwd)/scripts/prometheus.yml:/prometheus/prometheus.yml \
                prom/prometheus --web.enable-otlp-receiver

4. Set the telemetry environment variables:

   .. code-block:: bash

      export GPTME_TELEMETRY_ENABLED=true
      export OTLP_ENDPOINT=http://localhost:4317
      export PROMETHEUS_ADDR=0.0.0.0  # optional (default: localhost, use 0.0.0.0 for Docker access)
      export PROMETHEUS_PORT=8000

5. Run gptme:

   .. code-block:: bash

      poetry run gptme 'hello'
      # or gptme-server
      poetry run gptme-server

6. View data:

   - **Traces**: Jaeger UI at http://localhost:16686
   - **Metrics**: Prometheus UI at http://localhost:9090
   - **Raw metrics**: Direct metrics endpoint at http://localhost:8000/metrics

Once enabled, gptme will automatically:

- Trace function execution times
- Record token processing metrics
- Monitor request durations
- Instrument Flask and HTTP requests
- Expose Prometheus metrics at `/metrics` endpoint

The telemetry data helps identify:

- Slow operations and bottlenecks
- Token processing rates
- Tool execution performance
- Resource usage patterns

Available Metrics
~~~~~~~~~~~~~~~~~

.. note::

    These metrics are still merely planned and may not be available yet, or be available in a different form.

The following metrics are automatically collected:

- ``gptme_tokens_processed_total``: Counter of tokens processed by type
- ``gptme_request_duration_seconds``: Histogram of request durations by endpoint
- ``gptme_tool_calls_total``: Counter of tool calls made by tool name
- ``gptme_tool_duration_seconds``: Histogram of tool execution durations by tool name
- ``gptme_active_conversations``: Gauge of currently active conversations
- ``gptme_llm_requests_total``: Counter of LLM API requests by provider, model, and success status
- HTTP request metrics (from Flask instrumentation)
- OpenAI/Anthropic API call metrics (from LLM instrumentations)

Example Prometheus Queries
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. note::

    These queries are aspirational and won't actually work yet.

Here are some useful Prometheus queries for monitoring gptme:

.. code-block:: promql

   # Average tool execution time by tool
   rate(gptme_tool_duration_seconds_sum[5m]) / rate(gptme_tool_duration_seconds_count[5m])

   # Most used tools
   topk(10, rate(gptme_tool_calls_total[5m]))

   # LLM request success rate
   rate(gptme_llm_requests_total{success="true"}[5m]) / rate(gptme_llm_requests_total[5m])

   # Tokens processed per second
   rate(gptme_tokens_processed_total[5m])

   # Active conversations
   gptme_active_conversations

   # Request latency percentiles
   histogram_quantile(0.95, rate(gptme_request_duration_seconds_bucket[5m]))

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

- ``GPTME_TELEMETRY_ENABLED``: Enable/disable telemetry (default: false)
- ``OTLP_ENDPOINT``: OTLP endpoint for traces (default: http://localhost:4317)
- ``PROMETHEUS_PORT``: Port for Prometheus metrics endpoint (default: 8000)
- ``PROMETHEUS_ADDR``: Address for Prometheus metrics endpoint (default: localhost, use 0.0.0.0 for Docker access)

Release
-------

To make a release, simply run ``make release`` and follow the instructions.
