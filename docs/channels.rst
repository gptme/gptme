:audience: user

Channels
========

Besides the terminal, the app, and your editor, gptme and the agents you build on it
can be reached through channels people already use: GitHub, email, chat apps, and
voice. Persistent agents like `Bob <https://github.com/TimeToBuildBob>`__ use
several of these to work with people and with each other.

- :doc:`bot` — mention ``@gptme`` in issues and pull requests to get answers or
  changes.
- :doc:`channels/email` — read and answer email, and message other agents over SSH
  with ``gptmail agent``.
- :doc:`channels/chat` — talk to an agent on Discord or WhatsApp.
- :doc:`channels/voice` — real-time voice conversations, including phone calls.

.. toctree::
   :hidden:

   bot
   channels/email
   channels/chat
   channels/voice

Apart from the GitHub bot, these live in
`gptme-contrib <https://github.com/gptme/gptme-contrib>`__. They were built for
gptme's own agents and are community-maintained, so expect rough edges.

Before connecting an agent to a channel, consider who can send it messages: anything
it reads can try to steer it. Restrict senders where the channel allows it, and see
:doc:`security`.
