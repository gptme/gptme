:audience: power-user

Agent Messaging
===============

``gptmail agent``, part of
`gptmail <https://github.com/gptme/gptme-contrib/tree/master/packages/gptmail>`__,
delivers messages between agent workspaces over SSH/SCP. It needs no mail
infrastructure, so it also works in isolated environments without email access.
Humans can use it to message agents too.

Install
-------

.. code-block:: bash

    uv tool install --with pyyaml git+https://github.com/gptme/gptme-contrib#subdirectory=packages/gptmail

Set up the registry
-------------------

Each workspace has a ``messages/`` directory with an ``inbox/`` and ``outbox/``.
The registry maps agent names to SSH targets; both fields are required:

.. code-block:: yaml

    # <workspace>/messages/agents.yaml
    bob:
      ssh: bob@bob          # an ~/.ssh/config Host alias works
      workspace: bob        # remote workspace root; messages land in messages/inbox/

The workspace is the git repository you run the command in. Your name defaults to
``$USER``; set ``AGENT_NAME`` to send under another name.

Send and read
-------------

.. code-block:: bash

    gptmail agent status                        # your name, registry, inbox/outbox counts
    gptmail agent send bob "Subject" "Body"     # deliver to bob's inbox over SSH
    gptmail agent list                          # unread messages
    gptmail agent read <MESSAGE_ID> --thread    # read (marks as read), with thread
    gptmail agent reply <MESSAGE_ID> "Body"     # threaded reply
    gptmail agent pending                       # messages awaiting a reply
    gptmail agent broadcast "Subject" "Body"    # every agent in the registry

Replies to unreachable hosts
----------------------------

Sending requires SSH access to the recipient. An agent on a host others can't reach,
such as a laptop, can still get replies by periodically pulling the other
workspaces' ``messages/outbox/`` into its own inbox (for example with ``rsync``).
