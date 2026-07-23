Server
======

gptme provides multiple web-based interfaces for browser-based interactions, from lightweight options to sophisticated desktop-integrated experiences.

Installation
------------

To use gptme's server capabilities, install with server extras:

.. code-block:: bash

    pipx install 'gptme[server]'

Start the server:

.. code-block:: bash

    gptme-server


For more CLI options, see the :ref:`CLI reference <cli:gptme-server>`.

.. _server:gptme-webui:

gptme-webui: Modern Web Interface
---------------------------------

The primary web interface is `gptme-webui <https://github.com/gptme/gptme/tree/master/webui>`_: a modern, feature-rich application that provides a complete gptme experience in your browser. (Originally a `standalone repo <https://github.com/gptme/gptme-webui>`_, now merged into the main gptme repository.)

**Try it now:**

- `chat.gptme.org <https://chat.gptme.org>`_ (latest version of gptme-webui, bring your own gptme-server)
- `gptme.ai <https://gptme.ai>`_ (upcoming hosted gptme service)

**Key Features:**

- Modern interface
- Streaming responses
- Mobile-friendly responsive design
- Dark mode support
- Conversation export and offline capabilities
- Integrated computer use interface
- Create your own persistent `agents`

**Local Installation:**
For self-hosting and local development, see the `gptme-webui README <https://github.com/gptme/gptme/tree/master/webui>`_.

To use the server with a locally hosted gptme-webui, configure the CORS origin when starting the server:

.. code-block:: bash

    gptme-server --cors-origin 'http://localhost:5701'

.. note::

    **Connecting the hosted web UI to a local server (Chrome 142+).**
    When you use the hosted web UI at `chat.gptme.org <https://chat.gptme.org>`_
    with a ``gptme-server`` running on ``localhost``, recent Chromium browsers
    (Chrome 142+) gate the connection behind a *Local Network Access* permission
    prompt. This check runs *before* CORS headers are evaluated, so the
    ``--cors-origin`` flag is necessary but no longer sufficient — you must also
    click **Allow** on the permission prompt for the page to reach your local
    server. Serving the web UI from a local origin (for example
    ``http://localhost:5701``) avoids the prompt entirely, since that is a
    local-to-local request.

.. note::

    **Host-header validation (DNS-rebinding protection).**
    On loopback binds the server runs without an auth token, so it validates
    the request ``Host`` header to block DNS-rebinding attacks (a malicious page
    whose hostname re-resolves to ``127.0.0.1`` would otherwise become
    same-origin with your local server and gain full unauthenticated API
    access, including shell execution via the agent). ``localhost``,
    ``127.0.0.1`` and ``[::1]`` (any port) are always allowed. If you proxy the
    local server behind a hostname, allow it with
    ``gptme-server serve --allowed-hosts gptme.local`` (comma-separated, or via
    ``GPTME_SERVER_ALLOWED_HOSTS``). Validation is skipped when auth is enabled
    (network binds) or explicitly disabled with ``GPTME_DISABLE_AUTH`` — those
    operators own their own access control (e.g. an authenticated ingress).

Self-Hosting with Docker Compose
--------------------------------

For a self-contained deployment, a ``docker-compose.yml`` is included at the
repository root. It builds a lean image (``scripts/Dockerfile.selfhost``,
gptme + the ``server`` extra only — no Node/agent tooling) and runs
``gptme-server`` with persistent volumes for config and conversation logs.

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/gptme/gptme.git
   cd gptme

   # Configure: at least one provider key is required
   cp .env.example .env
   $EDITOR .env

   # Build and start the server
   docker compose up --build

The server listens on http://localhost:5700.

The simplest way to use it is the basic web UI the server bundles at the same
origin — just open http://localhost:5700 in a browser. Being same-origin, it
needs no CORS setup (you will still need the server token; see below).

To use the full-featured hosted web UI instead, open
`chat.gptme.org <https://chat.gptme.org>`_ and point it at your server. That is
a cross-origin setup, so the server must allow the UI's origin — the default
``CORS_ORIGIN`` already permits ``chat.gptme.org``. Set ``CORS_ORIGIN`` in
``.env`` to a different origin if you self-host the web UI yourself.

Key ``.env`` settings:

- ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` / ``OPENROUTER_API_KEY`` — at least one is required.
- ``GPTME_SERVER_TOKEN`` — auth token. The server enables auth by default when bound to ``0.0.0.0`` (as in the container), so set this and configure the web UI with the same value. If left blank, a token is auto-generated at startup — find it with ``docker compose logs``.
- ``CORS_ORIGIN`` — origin allowed to call the server (defaults to the hosted web UI).
- ``GPTME_SERVER_PORT`` — host port to publish (the container always listens on 5700).

Production Deployment: nginx Reverse Proxy
------------------------------------------

The docker-compose setup above publishes the server on a plain HTTP port
(``5700`` by default). For a public-facing deployment you should put it behind a
reverse proxy that terminates TLS and forwards requests to the container. The
example below uses nginx with a Let's Encrypt certificate.

.. warning::

   Do **not** expose the raw ``5700`` port to the internet. Bind the published
   port to loopback so only the proxy can reach it. In ``.env`` (or
   ``docker-compose.yml``) set the publish address to ``127.0.0.1``:

   .. code-block:: yaml

      ports:
        - "127.0.0.1:5700:5700"

   Also set ``GPTME_SERVER_TOKEN`` to a strong value — the proxy handles TLS,
   but the token is what authenticates each request.

**1. Obtain a TLS certificate** with certbot (one-time, then auto-renewed):

.. code-block:: bash

   sudo apt install certbot python3-certbot-nginx
   sudo certbot certonly --nginx -d gptme.example.com

.. note::

   The ``--nginx`` plugin handles the ACME HTTP challenge through nginx
   itself, so you do **not** need to stop nginx first (unlike
   ``--standalone``, which binds its own listener to port 80 and fails
   when nginx is already running).

**2. nginx site config** (``/etc/nginx/sites-available/gptme``):

.. code-block:: nginx

   server {
       listen 80;
       server_name gptme.example.com;
       # Redirect all HTTP to HTTPS
       return 301 https://$host$request_uri;
   }

   server {
       listen 443 ssl;
       server_name gptme.example.com;

       ssl_certificate     /etc/letsencrypt/live/gptme.example.com/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/gptme.example.com/privkey.pem;

       # Restrict to TLS 1.2/1.3 with strong ciphers (Mozilla "intermediate" profile)
       ssl_protocols TLSv1.2 TLSv1.3;
       ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
       ssl_prefer_server_ciphers off;

       location / {
           proxy_pass http://127.0.0.1:5700;
           proxy_set_header Host              $host;
           proxy_set_header X-Real-IP         $remote_addr;
           proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;

           # gptme-server streams responses over Server-Sent Events
           # (text/event-stream). Disable proxy buffering and use long
           # read timeouts so streamed tokens are flushed to the client
           # immediately rather than buffered until the response completes.
           proxy_buffering    off;
           proxy_cache        off;
           proxy_read_timeout 3600s;
           proxy_set_header   Connection "";
           proxy_http_version 1.1;
       }
   }

**3. Enable the site and reload nginx:**

.. code-block:: bash

   sudo ln -s /etc/nginx/sites-available/gptme /etc/nginx/sites-enabled/
   sudo nginx -t        # validate config
   sudo systemctl reload nginx

The server is now reachable at ``https://gptme.example.com``. Point your web UI
(or the hosted UI at `chat.gptme.org <https://chat.gptme.org>`_) at that URL, and
set ``CORS_ORIGIN`` in ``.env`` to the origin the UI is served from.

.. note::

   The ``proxy_buffering off`` and long ``proxy_read_timeout`` settings are the
   important part: without them nginx buffers the SSE stream and the chat appears
   to hang until each full response is ready, instead of streaming token by
   token.

**Local-only access.** If you only want the server reachable from the host
itself (for example, behind a VPN or an SSH tunnel), skip the proxy entirely and
keep the default loopback bind — open an SSH tunnel from your client with
``ssh -L 5700:127.0.0.1:5700 user@host`` and use ``http://localhost:5700``.

Running as a systemd Service (pipx)
-----------------------------------

If you installed gptme directly with ``pipx`` rather than Docker, you can run
the server as a systemd service so it starts on boot and restarts on failure.
A ready-to-edit unit template ships at `scripts/gptme-server.service
<https://github.com/gptme/gptme/blob/master/scripts/gptme-server.service>`_.

The template runs the server as a dedicated ``gptme`` user, reads secrets from
``/etc/gptme/server.env``, binds loopback, and applies systemd hardening
(``ProtectSystem=strict``, ``NoNewPrivileges``, etc.). Install it with:

.. code-block:: bash

    # Dedicated service user + pipx install of the entrypoint
    sudo useradd --system --create-home --shell /usr/sbin/nologin gptme
    sudo -u gptme pipx install 'gptme[server]'

    # Pre-create config/data dirs (required: ProtectHome=read-only only bind-mounts
    # paths that already exist; gptme initialises them at import time, which fails
    # under the sandbox on a fresh install before any request is served)
    sudo -u gptme mkdir -p /home/gptme/.config/gptme \
                           /home/gptme/.local/share/gptme \
                           /home/gptme/.local/state/gptme

    # Secrets file (provider keys, optional GPTME_SERVER_TOKEN), not world-readable
    sudo install -d -m 750 -o gptme -g gptme /etc/gptme
    sudo install -m 640 -o gptme -g gptme /dev/null /etc/gptme/server.env
    sudoedit /etc/gptme/server.env   # add ANTHROPIC_API_KEY=... etc.

    # Download and install the unit (adjust User=, the ExecStart path, --cors-origin)
    sudo curl -fsSL https://raw.githubusercontent.com/gptme/gptme/master/scripts/gptme-server.service \
        -o /etc/systemd/system/gptme-server.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now gptme-server

Tail logs with ``journalctl -u gptme-server -f``. Because the unit binds
``127.0.0.1``, pair it with the nginx reverse proxy above to expose it over TLS;
on loopback the auth token is optional (set ``GPTME_SERVER_TOKEN`` in the env
file if you change the bind to a public interface).

Basic Web UI
------------

A lightweight chat interface with minimal dependencies is bundled with the gptme server for simple deployments.

Access at http://localhost:5700 after starting ``gptme-server``.

This interface provides basic chat functionality and is useful for:

- Quick testing and development
- Minimal server deployments
- Environments with limited resources

Computer Use Interface
----------------------

The computer use interface provides an innovative split-view experience with chat on the left and a live desktop environment on the right, enabling AI agents to interact directly with desktop applications.

.. include:: computer-use-warning.rst

**Docker Setup** (Recommended):

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/gptme/gptme.git
   cd gptme

   # Build and run the computer use container
   make build-docker-computer
   docker run -v ~/.config/gptme:/home/computeruse/.config/gptme -p 6080:6080 -p 8080:8080 gptme-computer:latest

**Access Points:**

- **Combined interface:** http://localhost:8080/computer
- **Chat only:** http://localhost:8080
- **Desktop only:** http://localhost:6080/vnc.html

**Features:**

- Split-view interface with real-time desktop interaction
- Toggle between view-only and interactive desktop modes
- Automatic screen scaling optimized for LLM vision models
- Secure containerized environment

**Requirements:**

- Docker with X11 support
- Available ports: 6080 (VNC) and 8080 (web interface)

Local Computer Use (Advanced)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can enable the ``computer`` tool locally on Linux systems, though this is not recommended for security reasons.

**Requirements:**

- X11 server
- ``xdotool`` package installed

**Usage:**

.. code-block:: bash

   # Enable computer tool in addition to default tools
   gptme --tools +computer

Set an appropriate screen resolution for your vision model before use.

For long-running visual workflows, prefer a specialized subagent profile to keep
parent context smaller:

.. code-block:: python

   # Desktop interaction (mouse, keyboard, screenshots)
   subagent(
       "computer-use",
       "Click the Submit button, wait for the modal, and screenshot the result",
   )

   # Web browsing and testing
   subagent(
       "browser-use",
       "Open localhost:5173, capture a screenshot, and report UI issues",
   )

Security
--------

This section describes the security model of gptme-server and the threat vectors it is designed (and not designed) to address.

.. _server:auth-model:

Authentication Model
~~~~~~~~~~~~~~~~~~~~

gptme-server uses a tiered authentication model based on the bind address:

- **Loopback bind** (``127.0.0.1`` / ``localhost`` / ``::1``, the default):
  Bearer auth is **disabled** — a random token is generated on startup but
  the server does not require it for API access. The assumption is that any
  process on the same machine is the legitimate user.

- **Network bind** (``0.0.0.0`` or an external IP): Bearer auth is
  **enabled** and required for every API request. Set ``GPTME_SERVER_TOKEN``
  to a fixed value; if unset the server generates one and prints it at
  startup.

- ``GPTME_DISABLE_AUTH`` overrides both: disables bearer checks entirely
  regardless of bind address. Use only behind an authenticated ingress.

.. warning::

   An authorized API client (anyone who can reach the server with a valid
   token, or the local user on a loopback bind) can execute arbitrary shell
   commands through the agent. There is no additional sandboxing at the
   API layer. The security boundary is **access to the server**, not any
   individual endpoint.

.. _server:threat-model:

Threat Model
~~~~~~~~~~~~

The server is hardened against two classes of browser-originating attack that
apply to the default unauthenticated loopback setup:

**1. Cross-Site Request Forgery (CSRF)**

A malicious web page cannot send credentialed cross-origin JSON requests
to the local server because the browser's CORS preflight blocks them: the
server does not return ``Access-Control-Allow-Origin`` headers for arbitrary
origins (only for the configured ``--cors-origin``). Plain forms can POST
without CORS, but cannot set ``Content-Type: application/json``, so the
server rejects them as malformed.

**2. DNS Rebinding**

A malicious site can re-resolve its hostname to ``127.0.0.1`` after the
page loads. That makes the browser consider it *same-origin* with the local
server, bypassing CORS entirely. The direct mitigation is **Host-header
validation** (see the note in the :ref:`gptme-webui <server:gptme-webui>`
section): the server rejects requests whose ``Host`` does not match
``localhost``, ``127.0.0.1``, ``::1``, the configured bind address, or any
explicitly allowed hostname (``--allowed-hosts``).

**What is NOT in scope:**

- *An authorized client redirecting the agent to sensitive files:* An
  authorized API client can already run ``cat /etc/passwd`` through the
  agent. Restricting which directories the agent *starts* in adds no
  security boundary — the agent can navigate anywhere from there.

- *Protecting one user from another on a shared server:* gptme-server is
  single-user by design. Multi-user setups must gate access at the network
  or OS level.

.. _server:workspace-patch:

Workspace PATCH Semantics
~~~~~~~~~~~~~~~~~~~~~~~~~

Conversations have a *workspace* — the directory the agent treats as its
working directory. The API exposes two operations that touch workspace:

- **PUT** ``/api/v2/conversations/<id>`` (create): accepts any
  ``workspace`` the client supplies, including paths outside the
  conversation's log directory. Rationale: an authorized client can already
  run arbitrary shell commands; workspace containment at creation adds no
  security boundary. Cloud pods and the workspace picker both rely on this
  to set a custom workspace on create.

- **PATCH** ``/api/v2/conversations/<id>/config`` (update): accepts the
  **round-trip of the already-persisted workspace** (the webui settings
  dialog sends the full config including workspace on every save), but
  **rejects any change that redirects the workspace outside the
  conversation's log directory**. Rationale: silently redirecting an
  existing agent's workspace mid-conversation to an arbitrary path is
  plausibly a confused-deputy attack vector and is never needed for
  legitimate updates. If the workspace must change, delete and recreate the
  conversation.

This creates an intentional asymmetry — create-any, re-target-never — to
minimize confused-deputy risk without breaking legitimate workflows.

REST API
--------

gptme-server provides a REST API for programmatic access to gptme functionality. This enables integration with custom applications and automation workflows.

The API endpoints support the core gptme operations including chat interactions, tool execution, and conversation management.

.. note::
   API documentation is available when running the server. Visit the server endpoint ``/api/docs/`` for interactive API documentation based on the OpenAPI spec (served at ``/api/docs/openapi.json``).
