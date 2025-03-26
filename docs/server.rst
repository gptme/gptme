Server
======

gptme has a minimal REST API with very minimalistic web UI.

.. note::
   The server and web UI is still in development and does not have all the features of the CLI.

To use it, you need to install gptme with ``server`` extras:

.. code-block:: bash

    pipx install 'gptme[server]'

It can then be started by running the following command:

.. code-block:: bash

    gptme-server

For more CLI usage, see the :ref:`CLI reference <cli:gptme-server>`.

There are a few different interfaces available:

Web UI
------

A tiny chat interface with minimal dependencies that is bundled with the server.

Simply start the server to access the interface at http://localhost:5000

Fancy Web UI
------------

A modern, feature-rich web interface for gptme is available as a separate project `gptme-webui <https://github.com/gptme/gptme-webui>`_. It is being built with `gptengineer.app <https://gptengineer.app>`_.

You can access it directly at `gptme.gptengineer.run <https://gptme.gptengineer.run>`_.

To serve gptme-webui locally, see the `README <https://github.com/gptme/gptme-webui>`_.

Features:

- Modern React-based interface with shadcn/ui
- Streaming of responses
- Mobile-friendly design
- Dark mode support
- Offline/exports support
- Computer use interface integration

Computer Use Interface
----------------------

The computer use interface provides a split view with a chat on the left and a desktop on the right.

Requires Docker.

.. include:: computer-use-warning.rst

To run the computer use interface in Docker, follow these steps:

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/gptme/gptme.git
   cd gptme
   # Build container
   make build-docker-computer
   # Run container
   docker run -v ~/.config/gptme:/home/computeruse/.config/gptme -p 6080:6080 -p 8080:8080 gptme-computer:latest

The computer use interface provides:

- Combined view at http://localhost:8080/computer
- Chat view at http://localhost:8080
- Desktop view at http://localhost:6080/vnc.html

Features:

- Split view with chat on the left, desktop on the right
- Toggle for view-only/interactive desktop mode
- Automatic screen scaling for optimal LLM vision

Requirements:

- Docker for running the server with X11 support
- Network ports 6080 (VNC) and 8080 (web UI) available

.. rubric:: Using Computer Use Locally

You can use the ``computer`` tool (which enables computer use) locally on Linux without Docker or VNC, but it is not recommended due to security concerns.

Requirements:
- X11 server
- ``xdotool`` installed

To enable the ``computer`` tool, specify a ``-t/--tools`` list that includes the computer tool (as it is disabled by default):

.. code-block:: bash

   gptme -t computer   # and whichever other tools you want

You also want to set a screen resolution that is suitable for the vision model you are using.
