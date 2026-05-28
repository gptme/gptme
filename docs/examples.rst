Examples
========

Here are some examples of how to use gptme and what its capabilities are.

To see example output without running the commands yourself, check out the :doc:`demos` page.

.. contents::
   :local:
   :depth: 2

Common Tasks
------------

Everyday prompts that work well with gptme out of the box.

.. code-block:: bash

    # ask questions about files
    gptme 'summarize this' README.md
    gptme 'refactor this' main.py
    gptme 'what do you see?' image.png  # vision

    # pipe stdin for context
    git status -vv | gptme 'fix TODOs'
    git status -vv | gptme 'commit'
    make test | gptme 'fix the failing tests'

    # explore the workspace
    gptme 'explore'
    gptme 'take a screenshot and tell me what you see'
    gptme 'suggest improvements to my vimrc'

    # read URLs and GitHub issues
    gptme 'implement this' https://github.com/gptme/gptme/issues/286
    gptme 'implement gptme/gptme/issues/286'  # uses `gh` shell tool

    # create new projects
    gptme 'create a performant n-body simulation in rust'
    gptme 'render mandelbrot set to mandelbrot.png'
    gptme 'write a web app to particles.html which shows off an impressive and colorful particle effect using three.js'

    # chaining prompts
    gptme 'make a change' - 'test it' - 'commit it'
    gptme 'show me something cool in the python repl' - 'something cooler' - 'something even cooler'

    # resume the last conversation
    gptme -r

Advanced Workflows
------------------

gptme's tool system lets you unlock more powerful workflows. Enable extra tools with the ``--tools`` flag.

.. rubric:: Subagents (Planner Mode)

Use a separate planning agent to research and plan before coding. This is great for complex tasks where you want clear reasoning before any code is written.

.. code-block:: bash

    gptme --tools +subagent \
      'Plan and implement a CLI tool that monitors CPU/memory usage and alerts when thresholds are exceeded'

The subagent researches the approach, presents a plan, and only then does gptme start writing code. The result is better architecture for complex projects.

.. rubric:: Computer Use

Let gptme interact with your desktop — take screenshots, move the mouse, click buttons, and type. Useful for GUI automation, testing, and workflows that span multiple applications.

.. code-block:: bash

    gptme --tools +computer \
      'Take a screenshot of my browser, identify any UI issues, and write a bug report to bugs.md'

.. rubric:: Combining Tools for Maximum Power

Enable multiple tools together for complex, autonomous workflows. The most powerful combination is ``+subagent`` (for planning) with ``+computer`` (for desktop interaction):

.. code-block:: bash

    # Plan, implement, and visually verify — all in one session
    gptme --tools +computer,+subagent \
      'Research the top Python testing frameworks, implement a comparison benchmark, run it, and take a screenshot of the results'

    # Autonomous agent workflow: plan first, then execute with full tool access
    gptme --tools +subagent,+computer,+browser \
      'Find my most-starred GitHub repo, write a blog post about it, and open the draft in my browser'

The subagent handles research and planning; ``+computer`` and ``+browser`` handle execution and verification.

.. rubric:: Setting Up a Persistent Agent (gptme-agent)

Create a persistent AI agent — like the example in :doc:`agents` — that runs autonomously, maintains its own task list, journal, and learns over time.

.. code-block:: bash

    # Install gptme (includes the gptme-agent command)
    pipx install gptme

    # Create a new agent workspace from the template
    gptme-agent create ~/my-agent --name MyAgent

    # Bootstrap it
    cd ~/my-agent
    gptme 'explore the workspace, read my identity files, and tell me what I am'

    # Run it autonomously on a schedule
    gptme-agent install
    gptme-agent run

Your agent will have its own workspace, task system, journal, and lesson system — everything needed for a persistent, self-improving AI agent.

.. rubric:: MCP Servers

Connect gptme to custom tools and data sources via the Model Context Protocol.

Configure MCP servers in ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [[mcp.servers]]
    name = "filesystem"
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-filesystem", "/projects"]
    auto_start = true

Then use gptme as usual — the server starts automatically:

.. code-block:: bash

    gptme 'Refactor all my unused imports across all projects under /projects'

See the :doc:`mcp` page for the full list of configuration options.

Automation
----------

gptme can be used in scripts and CI/CD pipelines for automated workflows. See the :doc:`automation` page for full examples.

.. code-block:: bash

    # Non-interactive mode for scripts
    git diff | gptme --non-interactive 'review this diff for bugs and security issues'
    gptme --non-interactive --model 'sonnet' 'generate a changelog to CHANGELOG.md from these commits' <<< "$(git log --oneline v1.0..HEAD)"

The :doc:`automation` page covers code review bots, daily activity summaries, and composable shell pipelines.

Community Extensions (gptme-contrib)
-------------------------------------

`gptme-contrib <https://github.com/gptme/gptme-contrib>`_ is a community repository with plugins, packages, and scripts that extend gptme with additional capabilities.

.. rubric:: Getting Started

Clone the repo and point gptme at it:

.. code-block:: bash

    git clone https://github.com/gptme/gptme-contrib ~/.config/gptme/contrib

Then enable plugins in your ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [plugins]
    paths = ["~/.config/gptme/contrib/plugins"]
    enabled = ["gptme_imagen"]

.. rubric:: Image Generation

The ``gptme-imagen`` plugin adds multi-provider image generation (DALL-E, Gemini Imagen):

.. code-block:: bash

    gptme 'generate an image of a futuristic city at night, save to city.png'
    gptme 'render the mandelbrot set as an image using matplotlib and compare it with an AI-generated version'

.. rubric:: Semantic Context Retrieval

The ``gptme-retrieval`` plugin automatically injects relevant context from your codebase before each step — useful when working on large projects:

.. code-block:: toml

    [plugins]
    enabled = ["gptme_retrieval"]

    [plugin.retrieval]
    backend = "qmd"     # semantic search (requires: cargo install qmd)
    mode = "vsearch"    # vector search
    max_results = 5

Browse the `full plugin list <https://github.com/gptme/gptme-contrib/tree/master/plugins>`_ — there are also plugins for LSP integration, multi-model consensus, code graph analysis (via ``gptme-codegraph``), voice, and more.

Explore More
------------

Learn more about gptme with these dedicated pages:

* :doc:`demos` — watch example runs with terminal recordings
* :doc:`automation` — CI/CD, cron jobs, shell scripts
* :doc:`Projects </projects>` — things built with gptme

Do you have a cool example? Share it with us in the `Discussions <https://github.com/gptme/gptme/discussions>`_!

.. toctree::
   :maxdepth: 2
   :caption: More Examples

   demos
   automation
   projects
