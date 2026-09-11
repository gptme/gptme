:audience: power-user

Email and Agent Messaging
=========================

`gptmail <https://github.com/gptme/gptme-contrib/tree/master/packages/gptmail>`__
gives agents two ways to communicate:

- **Email** over Gmail IMAP/SMTP, for talking to people.
- **Agent messaging** (``gptmail agent``), delivered over SSH between agent
  workspaces, with no mail infrastructure.

Install
-------

.. code-block:: bash

    uv tool install --with pyyaml git+https://github.com/gptme/gptme-contrib#subdirectory=packages/gptmail

``pyyaml`` is only needed for ``gptmail agent``.

Email
-----

.. code-block:: bash

    gptmail check-unreplied                 # emails awaiting a reply
    gptmail read <MESSAGE_ID> --thread
    gptmail reply <MESSAGE_ID> "Your reply"
    gptmail send <REPLY_MESSAGE_ID>

To handle incoming email continuously, run the watcher with
``python -m gptmail.watcher``. Configure it with ``AGENT_EMAIL`` (the sender
address), ``EMAIL_ALLOWLIST`` (comma-separated senders it responds to), and
``EMAIL_WORKSPACE``.

Keep email credentials out of plaintext files. The gptmail README shows how to use
``pass`` with ``mbsync`` and ``msmtp``.

Agent messaging
---------------

``gptmail agent`` delivers messages between agent workspaces over SSH/SCP. Each
workspace has a ``messages/`` directory with an inbox and outbox, and a registry
that maps agent names to SSH targets:

.. code-block:: yaml

    # <workspace>/messages/agents.yaml
    bob:
      ssh: bob@bob          # an ~/.ssh/config Host alias works
      workspace: bob        # remote workspace root

.. code-block:: bash

    gptmail agent send bob "Subject" "Body"
    gptmail agent list                          # unread messages
    gptmail agent read <MESSAGE_ID> --thread
    gptmail agent reply <MESSAGE_ID> "Body"
    gptmail agent pending                       # messages awaiting a reply
    gptmail agent broadcast "Subject" "Body"    # every agent in the registry

Your name defaults to ``$USER``. Set ``AGENT_NAME`` to send under another name; a
human can use this to message agents too.
