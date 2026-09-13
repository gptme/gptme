:audience: power-user

Chat Apps
=========

Discord
-------

The `Discord bot <https://github.com/gptme/gptme-contrib/tree/master/scripts/discord>`__
lets an agent talk with people on Discord, with access to gptme's tools.

1. Create an application in the
   `Discord developer portal <https://discord.com/developers/applications>`__, add
   a bot, and copy its token.

2. Copy ``.env.discord.example`` to ``.env.discord`` and set ``DISCORD_TOKEN``.
   Optional settings include ``DEFAULT_MODEL`` and ``RATE_LIMIT``.

See the bot's README for running it and for all options.

WhatsApp
--------

`gptme-whatsapp <https://github.com/gptme/gptme-contrib/tree/master/packages/gptme-whatsapp>`__
connects an agent to WhatsApp through
`whatsapp-web.js <https://github.com/pedroslopez/whatsapp-web.js>`__. Each
incoming message runs gptme (or Claude Code) in the agent's workspace, with a
separate conversation per sender.

It needs Node.js 18+ and a phone to link. On first run it prints a QR code to scan
under **Linked Devices**:

.. code-block:: bash

    cd packages/gptme-whatsapp/node && npm install
    GPTME_AGENT=myagent AGENT_WORKSPACE=/path/to/agent node index.js

Set ``ALLOWED_CONTACTS`` to restrict who can talk to the agent. See the package
README for the Claude Code backend and other options.
