System Dependencies
===================

Some gptme features require additional dependencies. These are optional and only needed for specific features.

Python Extras
-------------

gptme has optional Python dependencies that can be installed using extras:

.. code-block:: bash

    # Install with specific extras
    pipx install "gptme[server,browser]"

    # Install with all optional dependencies
    pipx install "gptme[all]"

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Extra
     - Description
   * - ``server``
     - Flask server for web UI and REST API
   * - ``browser``
     - Playwright for web browsing and automation
   * - ``datascience``
     - matplotlib, pandas, numpy for data analysis
   * - ``youtube``
     - YouTube transcript extraction
   * - ``tts``
     - Text-to-speech support with Kokoro
   * - ``telemetry``
     - OpenTelemetry instrumentation for observability
   * - ``all``
     - All optional dependencies

Installing from Source
----------------------

To install the latest development version from git:

.. code-block:: bash

    # Using pipx
    pipx install "git+https://github.com/gptme/gptme.git"

    # Using uv
    uv tool install "git+https://github.com/gptme/gptme.git"

    # With extras
    pipx install "git+https://github.com/gptme/gptme.git[server,browser]"

If you have cloned the repository locally and want an editable install (changes to code take effect immediately):

.. code-block:: bash

    # Clone if you haven't already
    git clone https://github.com/gptme/gptme.git
    cd gptme

    # Using pipx (editable)
    pipx install -e .

    # Using uv (editable)
    uv tool install -e .

    # Editable with extras
    pipx install -e ".[server,browser]"

Recommended
-----------

These packages enhance gptme's capabilities and are recommended for the best experience:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Dependency
     - Purpose
     - Installation
   * - ``shellcheck``
     - Shell script linting (used by pre-commit)
     - ``apt install shellcheck`` (Debian/Ubuntu) or ``brew install shellcheck`` (macOS)
   * - ``tmux``
     - Terminal multiplexer for long-running commands
     - ``apt install tmux`` (Debian/Ubuntu) or ``brew install tmux`` (macOS)
   * - ``gh``
     - GitHub CLI for the gh tool
     - See `GitHub CLI installation <https://cli.github.com/>`_

Optional System Packages
------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Dependency
     - Purpose
     - Installation
   * - ``playwright``
     - Browser automation for the browser tool
     - ``pipx inject gptme playwright && playwright install``
   * - ``lynx``
     - Text-based web browser (alternative to playwright)
     - ``apt install lynx`` (Debian/Ubuntu) or ``brew install lynx`` (macOS)
   * - ``wl-clipboard``
     - Wayland clipboard support
     - ``apt install wl-clipboard`` (Debian/Ubuntu)
   * - ``pdftotext``
     - PDF text extraction
     - ``apt install poppler-utils`` (Debian/Ubuntu) or ``brew install poppler`` (macOS)

Details
-------

playwright
~~~~~~~~~~

The ``playwright`` library enables browser automation capabilities. After installing with ``pipx inject gptme playwright``, run ``playwright install`` to download the required browser binaries.

lynx
~~~~

An alternative to playwright for web browsing. Uses less resources and works in text mode, but has limited JavaScript support.

wl-clipboard
~~~~~~~~~~~~

Needed for clipboard operations on Wayland-based Linux systems. Not required on X11 systems or other platforms.

pdftotext
~~~~~~~~~

Part of the poppler utilities, used for extracting text from PDF files. Install the ``poppler-utils`` package on Debian/Ubuntu or ``poppler`` on macOS.
