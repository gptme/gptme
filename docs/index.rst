gptme documentation
===================

Welcome to the documentation for ``gptme``!

``gptme`` is a personal AI assistant and agent platform that runs in your terminal and browser, equipped with powerful tools to execute code, edit files, browse the web, and more - acting as an intelligent copilot for your computer. The core components include:

- **gptme CLI**: The main :doc:`command-line interface <cli>` for terminal-based interactions
- **gptme-server**: A :doc:`server component <server>` for running gptme as a service
- **gptme-webui**: A :doc:`web interface <server>` for browser-based interactions
- **gptme-agent-template**: A template for creating custom :doc:`AI agents <agents>`

The system can execute python and bash, edit local files, search and browse the web, and much more through its rich set of :doc:`built-in tools <tools>` and extensible :doc:`tool system <custom_tool>`. You can see what's possible in the :doc:`examples` and :doc:`demos`, from creating web apps and games to analyzing data and automating workflows.

**Getting Started:** To begin using gptme, follow the :doc:`getting-started` guide, set up your preferred :doc:`LLM provider <providers>`, and customize your :doc:`configuration <config>` as needed.

The system is designed to be easy to use and extend, and can be used as a library, standalone application, or web service. For detailed usage patterns and features, see the :doc:`usage` guide.

See the `README <https://github.com/gptme/gptme/blob/master/README.md>`_ file for more general information about the project.

.. note::
    This documentation site is still under construction.

.. raw:: html

   <div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;max-width:100%;margin:1.5rem 0;border-radius:10px;border:1px solid rgba(128,128,128,0.2);box-shadow:0 4px 20px rgba(0,0,0,0.15);">
     <iframe src="https://bisque.cloud/p/github/gptme-gptme" title="gptme — narrated walkthrough" loading="lazy"
       style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;"
       allow="autoplay; fullscreen; encrypted-media" allowfullscreen></iframe>
   </div>

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   getting-started
   system-dependencies
   usage
   examples
   howto/index
   tools
   commands
   cli
   config
   providers
   model-routing
   security

.. toctree::
   :maxdepth: 2
   :caption: Agents & Extensibility

   features
   concepts
   glossary
   agents
   server
   mcp
   acp
   lessons
   skills

.. toctree::
   :maxdepth: 2
   :caption: Developer Guide

   contributing
   building
   custom_tool
   hooks
   plugins
   prompts
   api
   evals
   bot
   finetuning

.. toctree::
   :maxdepth: 2
   :caption: Design Documents

   design/hook-based-confirmations
   design/elicitation

.. toctree::
   :maxdepth: 2
   :caption: About

   alternatives
   arewetiny
   misc/acronyms
   timeline
   changelog

.. toctree::
   :caption: External
   :maxdepth: 2

   GitHub <https://github.com/gptme/gptme>
   Discord <https://discord.gg/NMaCmmkxWv>
   X <https://x.com/gptmeorg>



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
* `llms.txt <llms.txt>`_ and `llms-full.txt <llms-full.txt>`_
