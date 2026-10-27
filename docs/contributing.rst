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

   # install uv (if not installed)
   pipx install uv

   # build the project
   make build

You can now start ``gptme`` from your development environment using the regular commands.

You can also install it in editable mode with ``pipx`` using ``pipx install -e .`` which will let you use your development version of gptme regardless of venv.

Verify your development setup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Before making changes, run this smoke test from the repository root:

.. code-block:: bash

   make build
   uv run gptme --version
   uv run pytest tests/test_cli.py::test_version -q

The install must finish without errors, the CLI command must print a version,
and the focused test must report ``1 passed``. These checks do not require an
API key. They make no LLM calls on a fresh setup. If ``ANTHROPIC_API_KEY`` is
already set in the environment or gptme config, pytest's sessionstart hook may
send a one-token Anthropic quota probe before the selected test runs.

Keeping your environment in sync
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``uv.lock`` pins the toolchain, and ``.pre-commit-config.yaml`` pins the
hook versions separately. A virtualenv that has drifted from the lockfile will
disagree with CI, usually in confusing ways:

- ``make lint`` reports errors CI does not have, or misses errors CI finds —
  most often a lint rule that was added or removed between versions.
- ``pre-commit`` keeps reformatting a file you just formatted, and the commit
  aborts every time with "files were modified by this hook".

Run ``uv sync --frozen`` before concluding that a lint or test result is a
problem with the repository, and check the version you are actually reasoning
about:

.. code-block:: bash

   uv sync --frozen
   uv run ruff --version   # must match uv.lock

Two things worth knowing when this happens:

- **Let the hooks own formatting.** Do not run a formatter yourself and then
  commit. If your version differs from the pinned hook's, the two rewrite each
  other's output and every commit fails.
- **A commit rejected by a hook does not exist.** The hook output scrolls past
  and a subsequent ``git push`` succeeds while pushing nothing. Check
  ``git log -1`` before reporting a commit as made.

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

      uv sync --extra telemetry --frozen

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
      export OTEL_EXPORTER_OTLP_TIMEOUT=5000  # ms; optional, default is 10s

5. Run gptme:

   .. code-block:: bash

      uv run --extra telemetry gptme 'hello'
      # or gptme-server (use --host 0.0.0.0 so the Prometheus container can reach it)
      uv run --extra telemetry --extra server gptme-server --host 0.0.0.0

6. View data:

   - **Traces**: Jaeger UI at http://localhost:16686
   - **Metrics**: Prometheus UI at http://localhost:9090

Once enabled, gptme will automatically:

- Trace function execution times
- Record token processing metrics
- Monitor request durations
- Instrument Flask and HTTP requests
- Expose Prometheus metrics at ``/api/v0/metrics`` endpoint (requires ``prometheus_client``)

The telemetry data helps identify:

- Slow operations and bottlenecks
- Token processing rates
- Tool execution performance
- Resource usage patterns

Content capture
~~~~~~~~~~~~~~~

Enabling telemetry keeps model names, token counts and timing data, but defaults
OpenAI and Anthropic SDK prompt and response capture to off. To explicitly include
model inputs and outputs in traces sent to your configured collector, set
``TRACELOOP_TRACE_CONTENT=true`` before starting gptme. Unset or empty values default
to ``false``; explicit settings are preserved (only ``true``, case-insensitive,
enables capture).

The SDK instrumentors also honor the OpenTelemetry context value
``override_enable_content_tracing``. Code that explicitly sets this value to true
enables capture within that context, even with ``TRACELOOP_TRACE_CONTENT=false``.
This is a default for SDK model content, not a general redaction filter: other
telemetry, including tool and function error strings, may contain sensitive data.
Send telemetry only to a collector you trust.

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
- ``gptme_llm_cost_usd_total``: Counter of LLM API cost in USD by provider and model (replaces the old per-request ``cost`` label)
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

   # LLM spend per hour by model (USD)
   sum by (model) (increase(gptme_llm_cost_usd_total[1h]))

   # Tokens processed per second
   rate(gptme_tokens_processed_total[5m])

   # Active conversations
   gptme_active_conversations

   # Request latency percentiles
   histogram_quantile(0.95, rate(gptme_request_duration_seconds_bucket[5m]))

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

- ``GPTME_TELEMETRY_ENABLED``: Enable/disable telemetry (default: false)
- ``TRACELOOP_TRACE_CONTENT``: Include SDK model inputs/outputs in traces (default: false;
  explicit per-context overrides can also enable capture, see `Content capture`_)
- ``OTLP_ENDPOINT``: OTLP endpoint for traces and metrics (default: http://localhost:4318)
- ``OTEL_EXPORTER_OTLP_TIMEOUT``: OTLP exporter timeout in milliseconds (default: 10000)

Multiple Instances
~~~~~~~~~~~~~~~~~~

When running multiple gptme instances with telemetry enabled, they can all send data to the same OTLP endpoint without port conflicts:

.. code-block:: bash

   # All instances use the same configuration
   export GPTME_TELEMETRY_ENABLED=true
   export OTLP_ENDPOINT=http://your-collector:4318

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

Windows Authenticode signing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Windows releases are signed with an Authenticode certificate in the
``tauri-autoupdater-signing`` GitHub environment. This eliminates the SmartScreen
"Unknown publisher" warning and satisfies enterprise AppLocker policies.
Configure these secrets before running a tagged release:

- ``WINDOWS_CERTIFICATE``: base64-encoded PFX/PKCS12 certificate (EV OV preferred)
- ``WINDOWS_CERTIFICATE_PASSWORD``: password for the PFX file

To obtain a certificate, purchase an EV OV code-signing certificate from a
Microsoft-trusted CA (DigiCert, Sectigo, etc.). Export it as a ``.pfx`` file and
encode it:

.. code-block:: bash

   base64 -w0 your-certificate.pfx

Store the PFX and password in the project's offline recovery vault before
provisioning GitHub. The certificate is referenced by its thumbprint, which the
CI workflow extracts automatically at build time.

The release workflow signs two Windows artifacts before packaging:

1. The NSIS installer (``.exe``) — signed by the Tauri CLI via ``signtool``
   using the ``bundle.windows.certificateThumbprint`` config override.
2. The PyInstaller sidecar binary (``gptme-server-*.exe``) — signed with
   ``signtool`` before Tauri bundles it, since Tauri only signs the installer.

When ``WINDOWS_CERTIFICATE`` is absent the workflow falls back to the unsigned
path (SmartScreen warning present). To verify an embedded signature offline::

   signtool verify /pa gptme_X.Y.Z_x64-setup.exe

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
