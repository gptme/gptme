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
- `Morph`_ - Apply fast targeted edits using Morph Fast Apply

💻 Code & Development
^^^^^^^^^^^^^^^^^^^^^

- `Python`_ - Execute Python code interactively with full library access
- `Shell`_ - Run shell commands and manage system processes
- `GH`_ - Interact with GitHub issues, PRs, and repositories
- `Precommit`_ - Automatically run pre-commit checks after file saves
- `Autocommit`_ - Automatically prompt for git commits after file modifications

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

🤝 User Interaction
^^^^^^^^^^^^^^^^^^^

- `Choice`_ - Present multiple-choice options to the user
- `Elicit`_ - Request structured single-field input from the user
- `Form`_ - Present a multi-field form for structured user input

⚡ Advanced Workflows
^^^^^^^^^^^^^^^^^^^^^

- `Tmux`_ - Manage long-running processes in terminal sessions
- `Subagent`_ - Delegate subtasks to specialized agent instances
- `Complete`_ - Signal that the autonomous session is finished
- `Restart`_ - Restart the gptme process after configuration changes
- `Vent`_ - Emit in-the-moment friction signals to a durable ledger

🧠 Knowledge & Planning
^^^^^^^^^^^^^^^^^^^^^^^

- `Lessons`_ - Access contextual lessons and behavioral guidance
- `Todo`_ - Manage a conversation-scoped working memory task list

🔌 Extensions
^^^^^^^^^^^^^

- `MCP`_ - Discover and connect Model Context Protocol servers

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

Morph
-----

.. automodule:: gptme.tools.morph
    :members:
    :noindex:

.. _gh:

GH
--

.. automodule:: gptme.tools.gh
    :members:
    :noindex:

Choice
------

.. automodule:: gptme.tools.choice
    :members:
    :noindex:

Elicit
------

.. automodule:: gptme.tools.elicit
    :members:
    :noindex:

Form
----

.. automodule:: gptme.tools.form
    :members:
    :noindex:

Precommit
---------

.. automodule:: gptme.tools.precommit
    :members:
    :noindex:

Autocommit
----------

.. automodule:: gptme.tools.autocommit
    :members:
    :noindex:

Vent
----

.. automodule:: gptme.tools.vent
    :members:
    :noindex:

Complete
--------

.. automodule:: gptme.tools.complete
    :members:
    :noindex:

Restart
-------

.. automodule:: gptme.tools.restart
    :members:
    :noindex:

Lessons
-------

.. automodule:: gptme.tools.lessons
    :members:
    :noindex:

Todo
----

.. automodule:: gptme.tools.todo
    :members:
    :noindex:

MCP
---

The Model Context Protocol (MCP) allows you to extend gptme with custom tools through external servers.
See :doc:`mcp` for configuration and usage details.

.. automodule:: gptme.tools.mcp
    :members:
    :noindex:
