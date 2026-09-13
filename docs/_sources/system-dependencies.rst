:audience: user

Extras and System Dependencies
==============================

The base ``gptme`` install works on its own. Some features need optional
Python extras or system packages — install them only for the features you use.

Python extras
-------------

Add extras in brackets when installing:

.. code-block:: bash

    # Specific extras
    pipx install "gptme[server,tui]"
    uv tool install "gptme[browser]"

    # Everything commonly needed (see below for what "all" covers)
    pipx install "gptme[all]"

    # With the one-line installer (default extras: browser)
    curl -sSf https://gptme.ai/install.sh | sh -s -- --extras browser,tui

.. list-table::
   :header-rows: 1
   :widths: 18 52 30

   * - Extra
     - What it enables
     - Read more
   * - ``server``
     - Web UI and REST API server (``gptme-server``)
     - :doc:`server`
   * - ``tui``
     - Textual-based terminal UI (``gptme-tui``)
     - :doc:`tui`
   * - ``acp``
     - Agent Client Protocol server for editors such as Zed
     - :doc:`acp`
   * - ``browser``
     - Playwright for the browser tool (browser binaries are installed
       separately, see `Browser binaries`_)
     - :doc:`tools/browser`
   * - ``docs``
     - ``lxml`` and ``pypdf`` for more robust XML/HTML parsing and PDF text
       extraction
     -
   * - ``datascience``
     - matplotlib, pandas, and numpy for the Python tool
     -
   * - ``computer``
     - No extra Python packages; for accessibility-first control of native
       Linux apps, install ``python3-pyatspi`` (see system packages)
     - :doc:`howto/computer-use`
   * - ``sounds``
     - ``sounddevice`` and ``scipy``, a Python fallback for audio playback
       (tool sounds) when no system audio player is available
     -
   * - ``sandbox``
     - Experimental Wasmtime backend for sandboxed Python (``GPTME_SANDBOX=wasmtime``);
       the firejail, bwrap, and docker backends use those programs instead
     -
   * - ``telemetry``
     - OpenTelemetry instrumentation for tracing and metrics
     -
   * - ``all``
     - The ``server``, ``acp``, ``tui``, ``browser``, ``docs``,
       ``datascience``, ``sounds``, and ``telemetry`` extras, plus ``dspy``
       and ``sentence-transformers``. It does **not** include ``sandbox``,
       ``eval``, ``swebench``, ``pyinstaller``, or the server's
       ``prometheus-client`` dependency.
     -

.. note::

   A few extras are only for development and evaluation: ``dspy`` (prompt
   optimization), ``eval`` (the full :doc:`evals` suite), ``swebench`` (the
   SWE-bench lane only), and ``pyinstaller`` (:doc:`building`). The ``minimal``
   extra adds no packages; it marks installs that avoid native extensions
   beyond ``pydantic-core`` (ARM, musl, Termux, Lambda).

Browser binaries
----------------

The ``browser`` extra installs the Playwright library, but not the browsers it
drives. gptme tries to download them automatically; to do it yourself, use the
same Playwright version that gptme installed:

.. code-block:: bash

    PW_VERSION=$(pipx runpip gptme show playwright | grep Version | cut -d' ' -f2)
    pipx run playwright==$PW_VERSION install chromium-headless-shell

    # or Firefox, for pages that block headless Chromium
    pipx run playwright==$PW_VERSION install firefox

See :doc:`tools/browser` for engine options.

Installing from source
----------------------

To install the latest development version from git:

.. code-block:: bash

    # Using pipx
    pipx install "git+https://github.com/gptme/gptme.git"

    # Using uv
    uv tool install "git+https://github.com/gptme/gptme.git"

    # With extras
    pipx install "gptme[server,browser] @ git+https://github.com/gptme/gptme.git"

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

System packages
---------------

These are picked up automatically when present on your ``PATH``.

.. list-table::
   :header-rows: 1
   :widths: 20 45 35

   * - Package
     - Used for
     - Installation
   * - ``tmux``
     - The tmux tool, for long-running and interactive commands
     - ``apt install tmux`` / ``brew install tmux``
   * - ``gh``
     - The gh tool, for GitHub issues, PRs, and CI
     - See `GitHub CLI installation <https://cli.github.com/>`_
   * - ``shellcheck``
     - Linting shell commands before the shell tool runs them
     - ``apt install shellcheck`` / ``brew install shellcheck``
   * - Playwright browsers
     - The browser tool's Playwright backend (requires the ``browser`` extra)
     - See :doc:`tools/browser`
   * - ``lynx``
     - Text-only browser fallback when Playwright is not installed
     - ``apt install lynx`` / ``brew install lynx``
   * - ``pdftotext``
     - PDF text extraction when ``pypdf`` (``docs`` extra) is not installed
     - ``apt install poppler-utils`` / ``brew install poppler``
   * - ``wl-clipboard`` or ``xclip``
     - Clipboard support on Linux (Wayland or X11)
     - ``apt install wl-clipboard`` / ``apt install xclip``
   * - ``python3-pyatspi``
     - Accessibility-first control of native Linux apps with the computer tool
     - ``apt install python3-pyatspi``
