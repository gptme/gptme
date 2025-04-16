Tools
=====

Tools available in gptme.

The tools can be grouped into the following categories:

- Execution

  - `Shell`_ - Run shell commands
  - `Python`_ - Run Python code interactively
  - `Tmux`_ - Use tmux to run commands
  - `Subagent`_ - Delegate task to a subagent

- Files

  - `Read`_ - Read files
  - `Save`_ - Save whole files
  - `Patch`_ - Patch files

- Network

  - `Browser`_ - Access the web

- Vision

  - `Vision`_ - View images
  - `Screenshot`_ - Take screenshots
  - `Computer`_ - Control the computer

- Other

  - `Chats`_ - Search past conversations
  - `RAG`_ - Retrieve documents and code
  - `TTS`_ - Speech synthesis for responses

Shell
-----

.. automodule:: gptme.tools.shell
    :members:
    :noindex:

Python
------

.. automodule:: gptme.tools.python
    :members:
    :noindex:

Tmux
----

.. automodule:: gptme.tools.tmux
    :members:
    :noindex:

Subagent
--------

.. automodule:: gptme.tools.subagent
    :members:
    :noindex:

Read
----

.. automodule:: gptme.tools.read
    :members:
    :noindex:

Save
----

.. automodule:: gptme.tools.save
    :members:
    :noindex:

Patch
-----

.. automodule:: gptme.tools.patch
    :members:
    :noindex:

Vision
------

.. automodule:: gptme.tools.vision
    :members:
    :noindex:

Screenshot
----------

.. automodule:: gptme.tools.screenshot
    :members:
    :noindex:

Browser
-------

.. automodule:: gptme.tools.browser
    :members:
    :noindex:

Chats
-----

.. automodule:: gptme.tools.chats
    :members:
    :noindex:

Computer
--------

.. include:: computer-use-warning.rst

.. automodule:: gptme.tools.computer
    :members:
    :noindex:

.. _rag:

RAG
---

.. automodule:: gptme.tools.rag
    :members:
    :noindex:

TTS
---

.. automodule:: gptme.tools.tts
    :members:
    :noindex:

MCP
---

The Model Context Protocol (MCP) allows you to extend gptme with custom tools through external servers.
See :doc:`mcp` for configuration and usage details.
