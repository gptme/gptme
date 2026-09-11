:audience: user

Command-line Interface
======================

gptme ships a set of command-line tools. Most of the time you only need ``gptme``
itself; the others cover setup, utilities, agents, and evaluation. Every tool accepts
``--help``, and the reference pages list all of their options.

**Chat and interfaces**

- :doc:`gptme <cli/gptme>` — the main CLI: chat with the assistant in your terminal.
- :doc:`gptme-server <cli/gptme-server>` — the web UI and REST API server; see :doc:`server`.
- :doc:`gptme-tui <cli/gptme-tui>` — the interactive terminal UI; see :doc:`tui`.
- :doc:`gptme-mcp-server <cli/gptme-mcp-server>` — expose gptme's tools as an MCP server over stdio; see :doc:`mcp`.
- ``gptme-acp`` — run gptme as an Agent Client Protocol agent for editors; see :doc:`acp`.

**Utilities**

- :doc:`gptme-util <cli/gptme-util>` — utility subcommands for models, tools, profiles, memory, and more.
- ``gptme-wut`` — start a session with the content of the current tmux pane.
- ``gptme-convert`` — convert files between formats (PDF, images, and more) offline.
- ``gptme-attest`` — generate and verify output attestations.

**Agents** (:doc:`reference <cli/agents>`)

- ``gptme-agent`` — create and manage autonomous agents; see :doc:`agents`.
- ``gptme-service`` — scaffold a persistent headless agent as a systemd or launchd service.

**Setup and diagnostics** (:doc:`reference <cli/setup>`)

- ``gptme-onboard`` — first-run setup wizard.
- ``gptme-auth`` — sign in to subscription providers and the gptme managed service.
- ``gptme-init`` — create a new gptme project scaffold.
- ``gptme-tutorial`` — interactive tutorial: learn gptme by doing.
- ``gptme-doctor`` — run system diagnostics.

**Sessions** (:doc:`reference <cli/sessions>`)

- ``gptme-status`` — generate a portable session-status document for handing off work.
- ``gptme-resume`` — rebuild a resume prompt from a prior session.
- ``gptme-checkpoint`` — lightweight recovery markers for Git workspaces.
- ``gptme-stats`` — LLM usage and cost statistics across all conversations.

**Evaluation and training** (:doc:`reference <cli/evaluation>`)

- ``gptme-eval`` — run gptme's eval suites; see :doc:`evals`.
- ``gptme-eval-swebench`` — run the SWE-bench evaluation.
- ``gptme-eval-tbench`` — run the Terminal-Bench evaluation.
- ``gptme-dataset`` — build fine-tuning datasets from session trajectories; see :doc:`finetuning`.
- ``gptme-eval-trends`` — track eval result trends and regressions over time (no reference page).
- ``gptme-dspy`` — optimize system prompts with DSPy (no reference page).

.. toctree::
   :hidden:

   cli/gptme
   cli/gptme-server
   cli/gptme-tui
   cli/gptme-util
   Agent Commands <cli/agents>
   cli/gptme-mcp-server
   cli/setup
   cli/sessions
   cli/evaluation

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
