:audience: power-user

Browser
=======

gptme includes a browser tool that lets the assistant load pages, read their
content, take screenshots, and interact with web pages.

.. automodule:: gptme.tools.browser
    :members:
    :noindex:

Backends
--------

Playwright (recommended)
~~~~~~~~~~~~~~~~~~~~~~~~

Full browser automation with screenshots, ARIA snapshots, clicking, form
filling, and scrolling.

**Installation:**

.. code-block:: bash

    pipx install 'gptme[browser]'
    PW_VERSION=$(pipx runpip gptme show playwright | grep Version | cut -d' ' -f2)
    pipx run playwright==$PW_VERSION install chromium-headless-shell

Lynx
~~~~

Text-only fallback for basic page reading and web search.  No screenshot
support.

.. code-block:: bash

    # Ubuntu / Debian
    sudo apt install lynx
    # macOS
    brew install lynx

Engine configuration (``GPTME_BROWSER_ENGINE``)
-----------------------------------------------

The Playwright backend accepts these forms for ``GPTME_BROWSER_ENGINE``:

.. list-table::
   :header-rows: 1

   * - Value
     - Meaning
   * - ``chromium`` (default)
     - Playwright's bundled Chromium headless shell
   * - ``firefox``
     - Playwright's bundled Firefox
   * - ``/path/to/binary``
     - Custom executable at a filesystem path
   * - ``executable-name``
     - Executable resolved via ``$PATH`` (``shutil.which``)

Custom executables (path or name) are always launched with the **Firefox**
engine so they receive the same Playwright browser-context options.

Use Firefox instead of Chromium
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Useful for pages that detect and block headless Chromium.

.. code-block:: bash

    PW_VERSION=$(pipx runpip gptme show playwright | grep Version | cut -d' ' -f2)
    pipx run playwright==$PW_VERSION install firefox
    export GPTME_BROWSER_ENGINE=firefox
    gptme "read https://example.com"

Use a fingerprint-patched Firefox (anti-detection)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some pages fingerprint headless browsers even when Firefox is used.
A patched build such as `Camoufox <https://camoufox.com/>`__ or
`invisible-playwright <https://github.com/QIN2DIM/undetected-playwright>`__
is a drop-in replacement that passes most fingerprint checks.

**By absolute path:**

.. code-block:: bash

    export GPTME_BROWSER_ENGINE=/usr/local/bin/camoufox-runner
    gptme "read https://bot-detection-test.vercel.app"

**By executable name on $PATH:**

.. code-block:: bash

    # Assuming camoufox is on your PATH
    export GPTME_BROWSER_ENGINE=camoufox
    gptme "screenshot https://example.com"

gptme detects that the value is not a named engine, resolves it via
``shutil.which``, and passes it to Playwright's ``executable_path=`` kwarg when
launching Firefox.

**Example: Camoufox setup**

.. code-block:: bash

    # Install Camoufox (fingerprint-patched Firefox)
    pip install camoufox
    python -m camoufox fetch           # downloads the patched Firefox binary

    # Point gptme at it
    export GPTME_BROWSER_ENGINE=$(python -m camoufox path)
    gptme "read https://example.com"

CDP mode (``GPTME_BROWSER_CDP_URL``)
------------------------------------

Connect to an already-running Chromium-based browser over the Chrome DevTools
Protocol instead of launching a new one.  Useful when you want to reuse an
existing authenticated browser session.

.. code-block:: bash

    # Start Chrome/Chromium with remote debugging
    chromium --remote-debugging-port=9222

    # Connect gptme to it
    export GPTME_BROWSER_CDP_URL=http://127.0.0.1:9222
    gptme "read https://example.com"

.. note::

   CDP only works with Chromium-based browsers.  ``GPTME_BROWSER_ENGINE``
   is ignored in CDP mode.

Session persistence (``GPTME_BROWSER_STORAGE_STATE``)
-----------------------------------------------------

Save login cookies and local storage so authenticated sessions persist across
restarts.

.. code-block:: bash

    # Log in once and save state
    gptme "open https://x.com/login and log in with user@example.com / hunter2, then save_browser_state ~/.config/gptme/twitter.json"

    # Reuse in the next session
    export GPTME_BROWSER_STORAGE_STATE=~/.config/gptme/twitter.json
    gptme "tweet 'hello from gptme'"

Environment variables
---------------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Default
     - Description
   * - ``GPTME_BROWSER_ENGINE``
     - ``chromium``
     - Engine or executable: ``chromium``, ``firefox``, a path, or a name on ``$PATH``
   * - ``GPTME_BROWSER_CDP_URL``
     - *(unset)*
     - WebSocket URL of an existing Chrome DevTools Protocol server
   * - ``GPTME_BROWSER_STORAGE_STATE``
     - *(unset)*
     - Path to a Playwright storage-state JSON file for persistent sessions

FAQ
---


**Does the browser tool bypass CAPTCHAs?**

No. The Playwright backend is a real browser engine (headless Chromium or Firefox),
so it behaves the same as any headless browser — some CAPTCHAs will block it.
gptme does not currently expose a headed-mode toggle for the built-in Playwright
launcher. To improve success on sites that detect headless Chromium, try Firefox:

.. code-block:: bash

    pipx run playwright==$PW_VERSION install firefox
    export GPTME_BROWSER_ENGINE=firefox

You can also connect to an existing Chromium-compatible browser over Chrome
DevTools Protocol:

.. code-block:: bash

    chromium --remote-debugging-port=9222
    export GPTME_BROWSER_CDP_URL=http://127.0.0.1:9222

**Can I use a full GUI browser with extensions?**

Yes — via the :doc:`/howto/computer-use` Docker image, which runs a real Chromium
browser inside a VNC-accessible desktop. Extensions, GUI interaction, and anything
that needs a visible browser window all work there. See the :doc:`Computer tool <computer>` and
:doc:`/howto/computer-use` for setup details.

**Can I run the browser tool inside Docker?**

The standard Playwright backend works in Docker (headless mode, no display
required). For headed/GUI mode inside Docker, use the computer-use Docker image
which bundles a VNC server and a full desktop environment. See
:doc:`/howto/computer-use` for details.

**The page is blocking my scrape — what should I try?**

In order:

1. Switch backends: ``GPTME_BROWSER_ENGINE=firefox`` (different fingerprint than
   Chromium)

2. Connect to an existing Chromium browser:
   ``GPTME_BROWSER_CDP_URL=http://127.0.0.1:9222``

3. Use Anthropic native search (Claude models only):
   ``GPTME_ANTHROPIC_WEB_SEARCH=true``

4. Use the Computer tool with the VNC Docker image for full GUI browser control
