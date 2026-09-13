:audience: user

.. _server:gptme-webui:

Web
===

The gptme web UI (`gptme-webui <https://github.com/gptme/gptme/tree/master/webui>`_)
is the richest way to work with gptme and your :doc:`agents <agents>`: a chat
interface that shows what the agent is doing and what it produced, rather than a
transcript alone.

Alongside the conversation it renders **artifacts** the agent created, **panels**
that tools declare at runtime (sandboxed iframes and live apps with their own
lifecycle), **browser and computer previews** of what the agent sees, tool
activity, and a branch map of the conversation. You can create and manage
persistent agents from it, and drive a full desktop through the
`Computer Use Interface`_.

Run it yourself with ``gptme-server`` (see :doc:`server`), use the :doc:`app`, or
sign in to :doc:`gptme.ai <cloud>`, where hosted instances can also expose a
running app on its own preview URL. (Originally a
`standalone repo <https://github.com/gptme/gptme-webui>`_, now merged into the main
gptme repository.)

**Key Features:**

- Streaming responses, with tool calls and their output shown inline
- Artifact and panel surfaces: previews of what the agent made and what its tools expose
- Integrated computer-use view
- Create and manage persistent :doc:`agents <agents>`
- Conversation history, search, branching, and export
- Mobile-friendly responsive design and dark mode

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

    **Cross-origin UIs and localhost (Chrome 142+).** When a web UI served from
    another origin connects to a ``gptme-server`` on ``localhost``, recent
    Chromium browsers gate the connection behind a *Local Network Access*
    permission prompt. That check runs *before* CORS headers are evaluated, so
    ``--cors-origin`` is necessary but not sufficient — you must also click
    **Allow**. Serving the UI from the server's own origin (the default) or from
    a local origin such as ``http://localhost:5701`` avoids the prompt.

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
