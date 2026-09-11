:audience: user

gptme documentation
===================

Welcome to the documentation for ``gptme``!

``gptme`` is a personal AI assistant and agent platform that runs in your terminal and browser, equipped with powerful tools to execute code, edit files, browse the web, and more - acting as an intelligent copilot for your computer. The core components include:

- **gptme CLI**: The main :doc:`command-line interface <cli>` for terminal-based interactions
- **gptme-server**: A :doc:`server component <server>` for running gptme as a service
- **gptme-webui**: A :doc:`web interface <server>` for browser-based interactions
- **gptme-agent-template**: A template for creating custom :doc:`AI agents <agents>`

The system can execute python and bash, edit local files, search and browse the web, and much more through its rich set of :doc:`built-in tools <tools>` and extensible :doc:`tool system <custom_tool>`. You can see what's possible in the :doc:`examples` and :doc:`demos`, from creating web apps and games to analyzing data and automating workflows.

See the `README <https://github.com/gptme/gptme/blob/master/README.md>`_ file for more general information about the project.

Where to start
--------------

- **New to gptme?** Install it with :doc:`getting-started`, learn the basics in :doc:`usage`, and try the :doc:`examples`.
- **Choose a model:** pick a :doc:`model <models>`, set up its :doc:`provider <providers>`, and tune gptme with its :doc:`configuration <config>`.
- **Pick an interface:** the :doc:`CLI <cli>`, the :doc:`TUI <tui>`, the :doc:`web UI <server>`, the :doc:`desktop app <desktop>`, your :doc:`editor <acp>`, or :doc:`GitHub <bot>`.
- **Give it context:** teach it with :doc:`lessons` and :doc:`skills`, and let it remember across sessions with :doc:`memory`.
- **Build on it:** run persistent :doc:`agents`, or extend gptme with :doc:`plugins`, :doc:`custom tools <custom_tool>`, :doc:`hooks`, and :doc:`MCP <mcp>`.
- **Contribute:** see :doc:`contributing`.

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Getting Started

   getting-started
   features
   usage
   examples

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Using gptme

   config
   models
   providers
   tools
   profiles
   commands
   automation
   security

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Interfaces

   cli
   tui
   server
   desktop
   acp
   bot

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Agents & Context

   agents
   lessons
   skills
   skills-gallery
   memory

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Extending gptme

   concepts
   plugins
   custom_tool
   hooks
   mcp

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Developer Guide

   contributing
   pr-lifecycle
   building
   api
   context-compression
   evals
   finetuning
   glossary
   design/index

.. toctree::
   :hidden:
   :maxdepth: 1
   :caption: About

   alternatives
   arewetiny
   misc/acronyms
   timeline
   changelog

.. toctree::
   :hidden:
   :caption: External

   GitHub <https://github.com/gptme/gptme>
   Discord <https://discord.gg/NMaCmmkxWv>
   X <https://x.com/gptmeorg>

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
* `llms.txt <llms.txt>`_ and `llms-full.txt <llms-full.txt>`_
