Tools
=====

gptme's tools enable AI agents to execute code, edit files, browse the web, process images, and interact with your computer.

Overview
--------

📁 File System
^^^^^^^^^^^^^^

- `Read`_ - Read files in any format
- `Save`_ - Create and overwrite files
- `Patch`_ - Apply precise changes to existing files

💻 Code & Development
^^^^^^^^^^^^^^^^^^^^^

- `Python`_ - Execute Python code interactively with full library access
- `Shell`_ - Run shell commands and manage system processes

🌐 Web & Research
^^^^^^^^^^^^^^^^^

- `Browser`_ - Browse websites, take screenshots, and read web content
- `RAG`_ - Index and search through documentation and codebases
- `Chats`_ - Search past conversations for context and references

👁️ Visual & Interactive
^^^^^^^^^^^^^^^^^^^^^^^

- `Vision`_ - Analyze images, diagrams, and visual content
- `Screenshot`_ - Capture your screen for visual context
- `Computer`_ - Control desktop applications through visual interface

⚡ Advanced Workflows
^^^^^^^^^^^^^^^^^^^^^

- `Tmux`_ - Manage long-running processes in terminal sessions
- `Subagent`_ - Delegate subtasks to specialized agent instances
- `TTS`_ - Convert responses to speech for hands-free interaction

Combinations
^^^^^^^^^^^^

The real power emerges when tools work together:

- **Web Research + Code**: `Browser`_ + `Python`_ - Browse documentation and implement solutions
- **Visual Development**: `Vision`_ + `Patch`_ - Analyze UI mockups and update code accordingly
- **System Automation**: `Shell`_ + `Python`_ - Combine system commands with data processing
- **Interactive Debugging**: `Screenshot`_ + `Computer`_ - Visual debugging and interface automation
- **Knowledge-Driven Development**: `RAG`_ + `Chats`_ - Learn from documentation and past conversations

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
