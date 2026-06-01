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

   sudo apt install certbot
   sudo certbot certonly --standalone -d gptme.example.com

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

REST API
--------

gptme-server provides a REST API for programmatic access to gptme functionality. This enables integration with custom applications and automation workflows.

The API endpoints support the core gptme operations including chat interactions, tool execution, and conversation management.

.. note::
   API documentation is available when running the server. Visit the server endpoint ``/api/docs/`` for interactive API documentation based on the OpenAPI spec (served at ``/api/docs/openapi.json``).
