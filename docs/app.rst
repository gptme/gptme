:audience: user

App
===

The gptme app packages the :ref:`web UI <server:gptme-webui>` as a native app for
macOS, Windows, Linux, and Android. It is built with
`Tauri <https://tauri.app/>`__, so every platform shares the same interface.

Install
-------

Download the build for your platform from the
`latest release <https://github.com/gptme/gptme/releases/latest>`__:

- **macOS, Windows, and Linux** (AppImage and ``.deb``): once installed, the app
  checks the releases for updates and updates itself.
- **Android**: install the APK from the release. It doesn't update itself; install
  newer releases the same way.

How it connects
---------------

- **Desktop:** on launch, the app starts its bundled ``gptme-server`` on
  localhost, or reuses a ``gptme-server`` that is already running, and opens the
  web UI in a native window. You don't need Python installed. The server is
  protected by a bearer token shared only with the app window.
- **Android:** the app can't run a local ``gptme-server``. In its setup wizard,
  sign in to :doc:`gptme.ai <cloud>`, or connect to your own ``gptme-server`` by
  entering its URL and auth token.

Since it's the same web UI, see :doc:`server` for what it can do and how to run a
server, and :doc:`providers` for setting up model access.

To build the app yourself, see
`tauri/README.md <https://github.com/gptme/gptme/blob/master/tauri/README.md>`__.
