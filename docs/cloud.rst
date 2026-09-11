:audience: user

Cloud
=====

`gptme.ai <https://gptme.ai>`__ is the managed gptme service: sign in, and gptme
runs for you in the cloud, with no install and no API keys of your own. It is
currently in early access.

What you get
------------

- **A hosted gptme instance** that you use through the web UI after signing in,
  with the same tools as a local install.
- **Model access through your account**, billed with usage-based credits, so you
  don't need keys from individual model providers.
- **GitHub integration**, so your instance can clone and work in your
  repositories.

Ways to connect
---------------

- **Browser:** sign in at `gptme.ai <https://gptme.ai>`__.
- **Desktop and Android apps:** choose cloud sign-in in the app's setup wizard. It
  opens gptme.ai in your browser and connects back to the app. See :doc:`app`.
- **Local CLI:** use gptme.ai for model access while running gptme on your own
  machine:

  .. code-block:: sh

      gptme-auth login
      gptme "hello" -m gptme/anthropic/claude-sonnet-4-6

  See :ref:`gptme-managed-service` for details.

Cloud or self-hosted?
---------------------

The cloud service runs the same gptme as a local install. Choose it when you want
gptme without managing installs, keys, or servers.

A hosted instance runs in its own environment rather than on your laptop, which
keeps an autonomous agent's blast radius away from your personal files and
credentials (see :doc:`security`). In exchange, your conversations and the
repositories you connect are processed by gptme.ai and its model providers.

To run everything on your own machines, or with your own provider keys, self-host
``gptme-server`` and the web UI instead. See :doc:`server`.
