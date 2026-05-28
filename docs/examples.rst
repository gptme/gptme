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

Create a persistent AI agent — like `Bob <https://github.com/TimeToBuildBob>`_ — that runs autonomously, maintains its own task list, journal, and learns over time.

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

.. code-block:: bash

    # Load a filesystem MCP server to work across projects
    gptme --mcp '{"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/projects"]}' \
      'Refactor all my unused imports across all projects under /projects'

Automation
----------

gptme can be used in scripts and CI/CD pipelines for automated workflows. See the :doc:`automation` page for full examples.

.. code-block:: bash

    # Non-interactive mode for scripts
    git diff | gptme --non-interactive 'review this diff for bugs and security issues'
    gptme --non-interactive --model 'sonnet' 'generate a changelog from these commits' <<< "$(git log --oneline v1.0..HEAD)"

The :doc:`automation` page covers code review bots, daily activity summaries, and composable shell pipelines.

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
