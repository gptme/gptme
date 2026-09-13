:audience: developer

Core
====

The building blocks of a conversation: messages, the code blocks inside them that
tools execute, and the log that stores, loads, and branches a conversation. See
:doc:`../usage` for managing conversations, and :doc:`../glossary` for terminology.

Message
-------

A message in the conversation.

.. autoclass:: gptme.message.Message
   :members:

Codeblock
---------

A codeblock in a message, possibly executable by tools.

.. automodule:: gptme.codeblock
   :members:

LogManager
----------

Holds the current conversation as a list of messages, saves and loads the conversation to and from files, supports branching, etc.

.. automodule:: gptme.logmanager
   :members:
