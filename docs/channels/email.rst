:audience: power-user

Email
=====

`gptmail <https://github.com/gptme/gptme-contrib/tree/master/packages/gptmail>`__
lets an agent read and answer email through your existing mail setup: ``mbsync``
fetches mail into a local Maildir and ``msmtp`` sends it. Every message is stored
as a Markdown file in the agent's workspace, and replies are tracked so each email
is answered once. The same package also provides :doc:`agent-messaging`.

Install
-------

.. code-block:: bash

    uv tool install git+https://github.com/gptme/gptme-contrib#subdirectory=packages/gptmail

Run ``gptmail`` from inside the agent's git workspace; it keeps mail under the
repository root and loads ``<workspace>/.env`` at startup.

Read and reply
--------------

.. code-block:: bash

    gptmail check-unreplied                 # emails awaiting a reply
    gptmail read <MESSAGE_ID> --thread
    gptmail reply <MESSAGE_ID> "Your reply"
    gptmail send <REPLY_MESSAGE_ID>
    gptmail mark-no-reply <MESSAGE_ID>      # handled, no reply needed
    gptmail --help                          # all commands

Handle incoming email
---------------------

To process incoming email unattended, run this on a schedule (cron, a systemd
timer, or the agent's own run loop):

.. code-block:: bash

    mbsync -a && gptmail sync-maildir && gptmail process-unreplied

``process-unreplied`` starts a gptme session for each unreplied email and takes a
per-message lock, so overlapping runs never handle the same email twice. Add
``--dry-run`` to preview. Configure it with:

- ``AGENT_EMAIL`` (required): the agent's address.
- ``EMAIL_ALLOWLIST``: comma-separated senders or domains the agent answers.
  Anyone who can email the agent can try to steer it, so keep this list tight.
- ``EMAIL_SEND_ALLOWLIST``: comma-separated recipients or domains the agent may
  send to.

A continuous watcher (``python -m gptmail.watcher``) also exists but is
experimental; see the gptmail README before relying on it.

Keep email credentials out of plaintext files. The
`gptmail README <https://github.com/gptme/gptme-contrib/tree/master/packages/gptmail>`__
shows how to use ``pass`` with ``mbsync`` and ``msmtp``, and lists every
configuration variable.
