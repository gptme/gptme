:audience: user

Command-line Interface
======================

gptme ships a set of command-line tools. Most of the time you only need ``gptme``
itself; the others cover setup, utilities, agents, and evaluation. Every tool accepts
``--help``, and the reference pages list all of their options.

**Chat and interfaces**

- :doc:`gptme <cli/gptme>` — the main CLI: chat with the assistant in your terminal.
- :doc:`gptme-tui <cli/gptme-tui>` — the interactive terminal UI; see :doc:`tui`.
- :doc:`gptme-server <cli/gptme-server>` — the web UI and REST API server; see :doc:`server`.
- :doc:`gptme-mcp-server <cli/gptme-mcp-server>` — expose gptme's tools as an MCP server over stdio; see :doc:`mcp`.
- ``gptme-acp`` — run gptme as an Agent Client Protocol agent for editors; see :doc:`acp`.

**Setup**

- :doc:`gptme-onboard <cli/gptme-onboard>` — first-run setup wizard.
- :doc:`gptme-auth <cli/gptme-auth>` — sign in to subscription providers and the gptme managed service.
- :doc:`gptme-init <cli/gptme-init>` — create a new gptme project scaffold.
- :doc:`gptme-tutorial <cli/gptme-tutorial>` — interactive tutorial: learn gptme by doing.
- :doc:`gptme-doctor <cli/gptme-doctor>` — run system diagnostics.

**Utilities**

- :doc:`gptme-util <cli/gptme-util>` — utility subcommands for models, tools, profiles, memory, and more.
- :doc:`gptme-status <cli/gptme-status>` — generate a portable session-status document for handing off work.
- :doc:`gptme-resume <cli/gptme-resume>` — rebuild a resume prompt from a prior session.
- :doc:`gptme-checkpoint <cli/gptme-checkpoint>` — lightweight recovery markers for Git workspaces.
- :doc:`gptme-stats <cli/gptme-stats>` — LLM usage and cost statistics across all conversations.
- ``gptme-wut`` — start a session with the content of the current tmux pane.
- ``gptme-convert`` — convert files between formats (PDF, images, and more) offline.
- ``gptme-attest`` — generate and verify output attestations.

**Agents**

- :doc:`gptme-agent <cli/gptme-agent>` — create and manage autonomous agents; see :doc:`agents`.
- :doc:`gptme-service <cli/gptme-service>` — scaffolding for a persistent headless agent service.

**Evaluation and training**

- :doc:`gptme-eval <cli/gptme-eval>` — run gptme's eval suites; see :doc:`evals`.
- :doc:`gptme-eval-swebench <cli/gptme-eval-swebench>` — run the SWE-bench evaluation.
- :doc:`gptme-eval-tbench <cli/gptme-eval-tbench>` — run the Terminal-Bench evaluation.
- ``gptme-eval-trends`` — track eval result trends and regressions over time.
- :doc:`gptme-dataset <cli/gptme-dataset>` — build fine-tuning datasets from session trajectories; see :doc:`finetuning`.
- ``gptme-dspy`` — optimize system prompts with DSPy.

.. toctree::
   :hidden:

   cli/gptme
   cli/gptme-tui
   cli/gptme-server
   cli/gptme-mcp-server
   cli/gptme-onboard
   cli/gptme-auth
   cli/gptme-init
   cli/gptme-tutorial
   cli/gptme-doctor
   cli/gptme-util
   cli/gptme-status
   cli/gptme-resume
   cli/gptme-checkpoint
   cli/gptme-stats
   cli/gptme-agent
   cli/gptme-service
   cli/gptme-eval
   cli/gptme-eval-swebench
   cli/gptme-eval-tbench
   cli/gptme-dataset

Common invocations
------------------

.. code-block:: bash

    gptme                                   # pick or start a conversation
    gptme "summarize this" README.md        # start with a prompt, and a file as context
    gptme -r                                # resume the most recent conversation
    gptme -n "run the tests and fix failures"   # non-interactive: no confirmations
    gptme --help                            # all options

See :doc:`usage` for a guided tour, :doc:`commands` for the slash commands available
inside a session, and :doc:`config` for configuration files and environment variables.
