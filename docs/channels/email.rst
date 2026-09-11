:audience: power-user

Email
=====

`gptmail <https://github.com/gptme/gptme-contrib/tree/master/packages/gptmail>`__
lets an agent read and answer email over Gmail IMAP/SMTP. The same package also
provides :doc:`agent-messaging`.

Install
-------

.. code-block:: bash

    uv tool install git+https://github.com/gptme/gptme-contrib#subdirectory=packages/gptmail

Read and reply
--------------

.. code-block:: bash

    gptmail check-unreplied                 # emails awaiting a reply
    gptmail read <MESSAGE_ID> --thread
    gptmail reply <MESSAGE_ID> "Your reply"
    gptmail send <REPLY_MESSAGE_ID>
    gptmail --help                          # all commands

Handle incoming email
---------------------

To process incoming email continuously, run the watcher with
``python -m gptmail.watcher`` (or ``--mode one`` to handle a single email and
exit). Configure it with:

- ``AGENT_EMAIL``: the sender address.
- ``EMAIL_ALLOWLIST``: comma-separated senders the agent responds to. Anyone who
  can email the agent can try to steer it, so keep this list tight.
- ``EMAIL_WORKSPACE``: the email workspace directory.

Keep email credentials out of plaintext files. The gptmail README shows how to use
``pass`` with ``mbsync`` and ``msmtp``.
