:audience: developer

Contributing
============

We welcome contributions to the project. Here is some information to get you started.

.. note::
    This document is a work in progress. PRs are welcome.

- :doc:`pr-lifecycle` — the current pull-request flow: what automation runs,
  what maintainers look for, and the response/merge timings contributors can expect.

- :doc:`building` — build standalone executables with PyInstaller.

- Looking to **add a new LLM provider**? See :doc:`providers-integration` for
  the dedicated guide covering plugin packages, custom provider configs, and
  built-in provider PRs.

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

Documentation
-------------

The docs are built with Sphinx from ``docs/`` (reStructuredText and MyST
Markdown). Build them locally with ``make docs``; CI treats warnings as errors.

Target audience
~~~~~~~~~~~~~~~

Every page declares who it is written for, as file-wide metadata at the very
top of the file (before any label or title):

.. code-block:: rst

   :audience: user

   Page Title
   ==========

In Markdown pages, use front matter:

.. code-block:: markdown

   ---
   audience: power-user
   ---

   # Page Title

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Audience
     - Written for
   * - ``user``
     - Anyone using gptme. Task- and outcome-focused; links to other pages for
       configuration details and internals.
   * - ``power-user``
     - Users configuring, automating, or integrating gptme: config keys,
       environment variables, provider specifics, edge cases.
   * - ``developer``
     - Contributors to gptme and authors of plugins, tools, and providers:
       internals, APIs, code paths, design rationale.

The build warns (and CI fails) when a page lacks a valid audience. Keep content
at the page's level: put power-user or developer detail on a more technical page
and link to it. Reviewers should flag content that doesn't match the page's audience.

Page structure
~~~~~~~~~~~~~~

The left sidebar lists pages; the right sidebar lists the headings of the
current page. Structure pages so both stay useful:

- **Hub pages** give a short overview and list their subpages near the top,
  with a brief descriptive list and a ``:hidden:`` toctree in the same place.
  Subpages then appear nested under the hub in the left sidebar.

- **Leaf pages** hold the content, have no toctree, and keep headings shallow
  (ideally two levels below the title), so the right sidebar reads as an outline.

- If a section should be reachable from the left sidebar, make it a subpage
  rather than a heading.

- Only nest a page under a hub whose content is an overview of its subpages.
  Don't nest a single related page under a content-heavy page: the sidebar
  then suggests that page is the parent's only subtopic. Link to it instead,
  and place it as a sibling.

- Don't add ``.. contents::`` directives; the right sidebar already shows the
  page outline.

- When moving or renaming a page, leave an ``:orphan:`` redirect stub at the old
  path (see ``docs/custom-providers.rst``) so external links keep working.

To see the structure as readers do, build the docs and run ``make docs-structure``:
it prints the left-sidebar tree with each page's headings. ``make docs`` also runs
``scripts/docs_structure.py --check``, which fails on broken sidebar links or pages
without a single title, and warns about skipped heading levels, overly deep or
duplicate headings, and content-heavy pages with a lone subpage.

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
      export OTLP_ENDPOINT=http://localhost:4318  # HTTP OTLP (port 4318)
      export GPTME_OTLP_METRICS=true  # Send metrics via OTLP

5. Run gptme:

   .. code-block:: bash

      poetry run gptme 'hello'
      # or gptme-server
      poetry run gptme-server

6. View data:

   - **Traces**: Jaeger UI at http://localhost:16686
   - **Metrics**: Prometheus UI at http://localhost:9090

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
- ``OTLP_ENDPOINT``: OTLP endpoint for traces and metrics (default: http://localhost:4318)
- ``GPTME_OTLP_METRICS``: Send metrics via OTLP instead of Prometheus HTTP (default: true)

Multiple Instances
~~~~~~~~~~~~~~~~~~

When running multiple gptme instances with telemetry enabled, they can all send data to the same OTLP endpoint without port conflicts:

.. code-block:: bash

   # All instances use the same configuration
   export GPTME_TELEMETRY_ENABLED=true
   export OTLP_ENDPOINT=http://your-collector:4318
   export GPTME_OTLP_METRICS=true

The OpenTelemetry Collector aggregates metrics from all instances and exports them to Prometheus on a single port that Prometheus can scrape.

**Benefits:**

- No port conflicts between instances
- Centralized telemetry collection and processing
- Single Prometheus scrape target (the collector)
- Works across network boundaries
- Supports traces and metrics through the same endpoint

Release
-------

To make a release, simply run ``make release`` and follow the instructions.

Android release signing
~~~~~~~~~~~~~~~~~~~~~~~

Android releases are signed with a long-lived upload key in the
``tauri-autoupdater-signing`` GitHub environment. Configure these secrets before
running a tagged release:

- ``ANDROID_KEYSTORE``: base64-encoded JKS or PKCS12 keystore
- ``ANDROID_KEY_ALIAS``: alias of the signing key
- ``ANDROID_KEY_PASSWORD``: password for the key entry
- ``ANDROID_STORE_PASSWORD``: password for the keystore

Also set the non-secret environment variable ``ANDROID_CERT_SHA256`` to the
signing certificate's SHA-256 fingerprint. Generate a key and obtain that value
with:

.. code-block:: bash

   keytool -genkeypair -keystore release.jks -alias gptme-release \
     -keyalg RSA -keysize 4096 -validity 10000 \
     -dname "CN=gptme, OU=release, O=gptme, C=SE"
   keytool -list -v -keystore release.jks -alias gptme-release

Store the keystore and passwords in the project's offline recovery vault before
provisioning GitHub. Losing this key prevents future releases from retaining the
same Android signing identity.

The release workflow refuses to upload Android artifacts when any signing value
is missing, signature verification fails, or the APK certificate does not match
``ANDROID_CERT_SHA256``. To rotate the key, first archive the old keystore,
create and back up the replacement, update all four secrets and the fingerprint
in one maintenance window, then verify the first release's APK with
``apksigner verify --verbose --print-certs``. Keep the previous public
fingerprint in the release notes so downstream metadata consumers can recognize
the intentional identity transition.

This signing strategy is intentionally shared with
`ActivityWatch/aw-android <https://github.com/ActivityWatch/aw-android>`_
(see ``scripts/sign_apk.sh`` and ``.github/workflows/build.yml`` there):
zipalign before signing, apksigner for APKs, jarsigner for AABs, and passwords
passed via environment variables. When changing the signing approach in either
project, apply the same change to the other to keep them consistent.

Note that gptme's Android app is built by Tauri (``tauri android build``, a
generated Gradle project) while aw-android is a native Android app — this does
not affect signing, since both sign the standard unsigned Gradle outputs after
the build. Signing deliberately happens in the tag-gated release job rather
than via a Gradle ``signingConfig`` or Tauri's keystore hooks in the build job,
so signing secrets are never exposed to PR-triggered builds.

Issue Labels
------------

We use a multi-dimensional labeling system to help contributors (both human and autonomous) find appropriate issues to work on.

Difficulty
~~~~~~~~~~

Indicates estimated effort required:

- ``difficulty: easy`` - Simple, well-scoped tasks (<4 hours)
- ``difficulty: medium`` - Moderate complexity (4-8 hours)
- ``difficulty: hard`` - Complex or architectural changes (>8 hours)

Status
~~~~~~

Shows the current state of an issue:

- ``status: ready`` - Fully specified, ready to start
- ``status: needs-design`` - Requires design decisions first
- ``status: blocked`` - Has dependencies or blockers
- ``status: in-progress`` - Someone is actively working
- ``status: has-pr`` - A pull request exists

Priority
~~~~~~~~

Indicates urgency and impact:

- ``priority: critical`` - Blocks users or development
- ``priority: high`` - Important for upcoming release
- ``priority: medium`` - Valuable but not urgent
- ``priority: low`` - Nice to have

Work Type
~~~~~~~~~

Special markers for contributor matching:

- ``autonomous-friendly`` - Suitable for AI agent work
- ``needs-human-judgment`` - Requires human decision-making
- ``good first issue`` - Good for new contributors
- ``help wanted`` - Community contributions welcome

Finding Issues to Work On
~~~~~~~~~~~~~~~~~~~~~~~~~

For quick wins:
  Filter: ``difficulty: easy`` + ``status: ready``

For substantial contributions:
  Filter: ``difficulty: medium`` + ``status: ready`` + ``autonomous-friendly``

For new contributors:
  Filter: ``good first issue`` + ``status: ready``

Before starting work on an issue, please comment to indicate you're working on it to avoid duplicate effort.
