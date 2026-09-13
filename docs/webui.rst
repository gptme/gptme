:audience: user

.. _server:gptme-webui:

Web
===

The gptme web UI (`gptme-webui <https://github.com/gptme/gptme/tree/master/webui>`_) is a
full chat interface in your browser, with streaming responses, conversation history,
and an integrated computer-use view. Run it yourself with ``gptme-server`` (see
:doc:`server`), use the :doc:`app`, or sign in to :doc:`gptme.ai <cloud>`.
(Originally a `standalone repo <https://github.com/gptme/gptme-webui>`_, now merged
into the main gptme repository.)

**Try it now:**

- `chat.gptme.org <https://chat.gptme.org>`_ (latest version of gptme-webui, bring your own gptme-server)
- :doc:`gptme.ai <cloud>` (managed hosted gptme service, early access)

**Key Features:**

- Modern interface
- Streaming responses
- Mobile-friendly responsive design
- Dark mode support
- Conversation export and offline capabilities
- Integrated computer use interface
- Create your own persistent `agents`

**Local use:**

The modern UI is bundled in gptme release packages. Run ``gptme-server`` and
open http://localhost:5700.

.. note::

   Release packages ship the modern UI, which ``gptme-server`` serves from
   ``gptme/server/webui-dist``. A source checkout without ``make bundle-webui``
   falls back to a minimal legacy page bundled in ``gptme/server/static``. That
   page is not a supported interface; its templates are currently also reused by
   the HTML export (``gptme-util chats export <id> -f html``).

For frontend development, see the `gptme-webui README <https://github.com/gptme/gptme/tree/master/webui>`_.
When Vite runs separately on port 5701, allow that development origin:

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

    **Host-header validation.** Bearer authentication is enabled for loopback
    and network binds alike. If an operator explicitly disables authentication
    with ``GPTME_DISABLE_AUTH``, they can still opt into Host-header validation
    with ``gptme-server serve --allowed-hosts gptme.local`` (comma-separated,
    or via ``GPTME_SERVER_ALLOWED_HOSTS``).

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
