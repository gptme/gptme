System Dependencies
===================

Some gptme features require additional non-Python dependencies. These are optional and only needed for specific tools.

Optional Dependencies
---------------------

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
   * - ``tmux``
     - Terminal multiplexer for long-running commands
     - ``apt install tmux`` (Debian/Ubuntu) or ``brew install tmux`` (macOS)
   * - ``gh``
     - GitHub CLI for the gh tool
     - See `GitHub CLI installation <https://cli.github.com/>`_
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

tmux
~~~~

Required for the tmux tool which enables running long-running or interactive commands in persistent terminal sessions.

gh (GitHub CLI)
~~~~~~~~~~~~~~~

The GitHub CLI is needed for the gh tool to interact with GitHub repositories, issues, and pull requests. Installation instructions vary by platform - see the `official documentation <https://cli.github.com/>`_.

wl-clipboard
~~~~~~~~~~~~

Needed for clipboard operations on Wayland-based Linux systems. Not required on X11 systems or other platforms.

pdftotext
~~~~~~~~~

Part of the poppler utilities, used for extracting text from PDF files. Install the ``poppler-utils`` package on Debian/Ubuntu or ``poppler`` on macOS.
