:audience: user

Examples
========

Start with the one-liners below to get a feel for gptme, follow a `how-to guide <How-to guides_>`_ when you have a specific task, and watch the :doc:`demos` to see it in action.

.. toctree::
   :hidden:

   demos

Quick examples
--------------

Everyday prompts that work well with gptme out of the box. gptme picks the right :doc:`tools <tools>` (shell, file editing, Python, browser, …) on its own — you describe the outcome, not the steps.

.. code-block:: bash

    # ask questions about files
    gptme 'summarize this' README.md
    gptme 'refactor this' main.py
    gptme 'what do you see?' image.png  # vision

    # compound questions that need several tools
    gptme "Read README.md, list the project's dependencies from pyproject.toml, and tell me if any are pinned to an old major version"

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

For interactive iteration, start a session and keep chatting:

.. code-block:: bash

    gptme  # opens an interactive session
    # > Read the failing tests and fix them
    # > Now run the test suite and show me the output
    # > Write a summary of what you changed to CHANGES.md

Tools run in your local environment with your permissions, so keep sessions scoped to the project directory. List the available tools with ``gptme-util tools list``, and see :doc:`security` before skipping confirmations with ``--no-confirm``.

How-to guides
-------------

Step-by-step recipes for common tasks, each with a copy-pasteable pattern you can adapt.

**Getting oriented**

- :doc:`howto/choose-workflow` — pick tools, model capability, local or remote execution, and an approval boundary from the outcome you need.
- :doc:`howto/minimal-context` — measure and trim prompt sections for cheaper, tighter specialized runs.

**Everyday coding**

- :doc:`howto/edit-files` — patch, save, and inspect without rewriting whole files.
- :doc:`howto/code-review` — review a diff, a PR, or a single file with context.
- :doc:`howto/debug-python` — reproduce, trace, and fix Python errors step by step.
- :doc:`howto/refactor` — rename, extract, and reshape code across a codebase.

**Automation and agents**

- :doc:`howto/automate-task` — turn a repeated shell or git procedure into a script.
- :doc:`howto/parallel-research` — fan research out to subagents and synthesize the results.
- :doc:`howto/computer-use` — control desktop apps and web UIs with screenshots, mouse, and keyboard.

**Extending**

- :doc:`howto/skills-workflows` — write project conventions once as skills and compose them into workflows.
- :doc:`howto/custom-tool-plugin` — give gptme a domain-specific tool as a Python plugin.

**Setup**

- :doc:`howto/tts-setup` — configure OpenRouter, the local gptme-tts server, or browser speech synthesis.

.. toctree::
   :hidden:

   Choose a Workflow <howto/choose-workflow>
   Minimal Context <howto/minimal-context>
   Edit Files <howto/edit-files>
   Review Code <howto/code-review>
   Debug Python <howto/debug-python>
   Refactor Code <howto/refactor>
   Automate Tasks <howto/automate-task>
   Parallel Research <howto/parallel-research>
   Computer Use <howto/computer-use>
   Reusable Skills <howto/skills-workflows>
   Custom Tool Plugin <howto/custom-tool-plugin>
   Text-to-Speech <howto/tts-setup>

Advanced workflows
------------------

Some tools are disabled by default. Enable them with the ``--tools`` flag to unlock more powerful workflows.

Subagents
~~~~~~~~~

Use subagents to research and plan before coding, or to split independent work across clean contexts:

.. code-block:: bash

    gptme --tools +subagent \
      'Plan and implement a CLI tool that monitors CPU/memory usage and alerts when thresholds are exceeded'

See :doc:`howto/parallel-research` for a fan-out/synthesize pattern, and the :doc:`subagent tool reference <tools/subagent>` for isolation, budgets, and structured output.

Computer use
~~~~~~~~~~~~

Let gptme interact with your desktop — take screenshots, move the mouse, click buttons, and type:

.. code-block:: bash

    gptme --tools +computer \
      'Take a screenshot of my browser, identify any UI issues, and write a bug report to bugs.md'

See :doc:`howto/computer-use` for prerequisites and recipes.

Combining tools
~~~~~~~~~~~~~~~

Enable several tools together for autonomous plan → execute → verify workflows:

.. code-block:: bash

    # Plan, implement, and visually verify — all in one session
    gptme --tools +computer,+subagent \
      'Research the top Python testing frameworks, implement a comparison benchmark, run it, and take a screenshot of the results'

    # Plan first, then execute with full tool access
    gptme --tools +subagent,+computer,+browser \
      'Find my most-starred GitHub repo, write a blog post about it, and open the draft in my browser'

MCP servers
~~~~~~~~~~~

Connect gptme to external tools and data sources via the Model Context Protocol. Configure servers in ``~/.config/gptme/config.toml``:

.. code-block:: toml

    [[mcp.servers]]
    name = "filesystem"
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-filesystem", "/projects"]
    auto_start = true

Then use gptme as usual — the server starts automatically:

.. code-block:: bash

    gptme 'Refactor all my unused imports across all projects under /projects'

See :doc:`mcp` for all configuration options.

Persistent agents
~~~~~~~~~~~~~~~~~

Create a persistent agent — with its own workspace, task list, journal, and lessons — that runs autonomously on a schedule:

.. code-block:: bash

    # Create a new agent workspace from the template
    gptme-agent create ~/my-agent --name MyAgent

    # Bootstrap it
    cd ~/my-agent
    gptme 'explore the workspace, read my identity files, and tell me what I am'

    # Run it autonomously on a schedule
    gptme-agent install
    gptme-agent run

See :doc:`agents` for how agents work.

Automation
----------

gptme runs in scripts, cron jobs, and CI/CD pipelines with ``--non-interactive``:

.. code-block:: bash

    git diff | gptme --non-interactive 'review this diff for bugs and security issues'
    gptme --non-interactive --model 'sonnet' 'generate a changelog to CHANGELOG.md from these commits' <<< "$(git log --oneline v1.0..HEAD)"

See :doc:`automation` for code review bots, scheduled summaries, and composable shell pipelines, and :doc:`bot` to run gptme from GitHub issues and pull requests.

Community extensions (gptme-contrib)
------------------------------------

`gptme-contrib <https://github.com/gptme/gptme-contrib>`_ is a community repository with plugins, packages, and scripts that extend gptme. Clone it and enable plugins in ``~/.config/gptme/config.toml``:

.. code-block:: bash

    git clone https://github.com/gptme/gptme-contrib ~/.config/gptme/contrib

.. code-block:: toml

    [plugins]
    paths = ["~/.config/gptme/contrib/plugins"]
    enabled = ["gptme_imagen"]

The ``gptme-imagen`` plugin adds multi-provider image generation (DALL-E, Gemini Imagen):

.. code-block:: bash

    gptme 'generate an image of a futuristic city at night, save to city.png'
    gptme 'render the mandelbrot set as an image using matplotlib and compare it with an AI-generated version'

The ``gptme-retrieval`` plugin automatically injects relevant context from your codebase before each step — useful on large projects:

.. code-block:: toml

    [plugins]
    enabled = ["gptme_retrieval"]

    [plugin.retrieval]
    backend = "qmd"     # semantic search (requires: cargo install qmd)
    mode = "vsearch"    # vector search
    max_results = 5

Browse the `full plugin list <https://github.com/gptme/gptme-contrib/tree/master/plugins>`_ — there are also plugins for LSP integration, multi-model consensus, code graph analysis (via ``gptme-codegraph``), voice, and more.

Demos and projects
------------------

- :doc:`demos` — terminal recordings of example runs.
- :doc:`projects` — things built with and powered by gptme.

Have a cool example? Share it in the `Discussions <https://github.com/gptme/gptme/discussions>`_!
