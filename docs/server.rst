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

The primary web interface is `gptme-webui <https://github.com/gptme/gptme-webui>`_: a modern, feature-rich React application that provides a complete gptme experience in your browser.

**Try it now:** `chat.gptme.org <https://chat.gptme.org>`_

**Key Features:**

- Modern React-based interface with shadcn/ui components
- Real-time streaming of AI responses
- Mobile-friendly responsive design
- Dark mode support
- Conversation export and offline capabilities
- Integrated computer use interface
- Full tool support and visualization

**Local Installation:**
For self-hosting and local development, see the `gptme-webui README <https://github.com/gptme/gptme-webui>`_.

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

   # Enable computer tool (disabled by default for security)
   gptme -t computer

Set an appropriate screen resolution for your vision model before use.

REST API
--------

gptme-server provides a REST API for programmatic access to gptme functionality. This enables integration with custom applications and automation workflows.

The API endpoints support the core gptme operations including chat interactions, tool execution, and conversation management.

.. note::
   API documentation is available when running the server. Visit the server endpoint ``/api/docs/`` for interactive API documentation based on the OpenAPI spec (served at ``/api/docs/openapi.json``).
