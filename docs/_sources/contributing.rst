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

3. Set the telemetry environment variable:

   .. code-block:: bash

      export GPTME_TELEMETRY_ENABLED=true
      export OTLP_ENDPOINT=http://localhost:4317  # optional (default)

4. Run gptme:

   .. code-block:: bash

      poetry run gptme 'hello'
      # or gptme-server
      poetry run gptme-server

5. View traces in Jaeger UI:

    You can view traces in the Jaeger UI at http://localhost:16686.

Once enabled, gptme will automatically:

- Trace function execution times
- Record token processing metrics
- Monitor request durations
- Instrument Flask and HTTP requests

The telemetry data helps identify:

- Slow operations and bottlenecks
- Token processing rates
- Tool execution performance

Release
-------

To make a release, simply run ``make release`` and follow the instructions.
