:audience: user

Desktop App
===========

The gptme desktop app packages the :ref:`web UI <server:gptme-webui>` together with
a bundled ``gptme-server``, giving you a standalone window without installing Python
or managing dependencies. It is built with `Tauri <https://tauri.app/>`__.

Install
-------

Download the build for your platform from the
`latest release <https://github.com/gptme/gptme/releases/latest>`__. Builds are
published for macOS, Windows, and Linux (AppImage and ``.deb``). Once installed, the
app checks those releases for updates and updates itself.

An Android build is published with each release as well. It can't run a local
``gptme-server``; connect it to a remote gptme instance instead.

How it works
------------

On launch, the app starts its bundled ``gptme-server`` on localhost — or reuses a
``gptme-server`` that is already running — and opens the web UI in a native window.
The server is protected by a bearer token shared only with the app window.

Since it runs the same server and web UI, see :doc:`server` for what the web UI can
do, and :doc:`providers` for setting up model access.

To build the app yourself, see
`tauri/README.md <https://github.com/gptme/gptme/blob/master/tauri/README.md>`__.
