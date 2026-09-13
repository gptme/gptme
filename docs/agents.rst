:audience: user

Agents
======

A gptme agent is a persistent assistant with its own identity, memory, and
workspace — a git repository that serves as its "brain" and holds everything it
knows and has done. It runs on a schedule, picks up work, and leaves a trail you
can read in the morning.

`Bob <https://github.com/TimeToBuildBob>`__ is the reference agent and the proof
that this works: he has been running autonomously since 2024 and helps develop
gptme itself — opening PRs, reviewing code, fixing CI, and managing his own task
queue.

What it does for you
--------------------

You point an agent at work and it goes and does it, on its own schedule, without
you in the loop for every step:

- **Keeps the PR queue moving** — reviews open PRs, fixes the CI failures it
  finds, and merges what passes.
- **Triages your issues** — reads new issues, reproduces them, and opens a PR
  with a fix or a diagnosis.
- **Watches production** — checks logs and monitoring, and reports (or files) the
  errors it discovers.
- **Dogfoods your app** — exercises real flows through the
  :doc:`browser or computer tools <tools>` and tells you what broke.
- **Works its own backlog** — picks the next task off its queue, does it, and
  updates the task when it's done.
- **Runs the task system itself** — files follow-ups it discovers along the way,
  so the backlog reflects reality instead of your last grooming session.

Clear goals are what turn this from a reactive tool into something that takes
initiative: an agent with a purpose can prioritize, notice opportunities, and
keep a long-term direction between runs.

None of this is free of judgment. An agent left alone does what its prompt,
tools, and credentials allow — see :doc:`security` for how to keep the blast
radius small, and :doc:`agents/autonomous` for the practical guardrails.

Get started
-----------

An agent workspace needs ``git``, ``python3``, `pipx <https://pipx.pypa.io/>`_,
`uv <https://docs.astral.sh/uv/>`_, and gptme itself:

.. code-block:: bash

    pipx install gptme uv

    # Create a workspace from the agent template, customized for your agent
    gptme-agent create ~/my-agent --name MyAgent

    # Bootstrap it: let the agent read its own identity files
    cd ~/my-agent
    gptme 'explore the workspace, read my identity files, and tell me what I am'

To let it run on a schedule through systemd or launchd:

.. code-block:: bash

    gptme-agent install      # install services
    gptme-agent status       # check state
    gptme-agent run          # trigger a run now
    gptme-agent logs -f      # watch what it does

``gptme-agent doctor`` checks a workspace's health, and ``gptme-agent scan``
lists agent processes running on the host. See :doc:`agents/autonomous` for
schedules, guardrails, and what makes a good autonomous prompt, and
:doc:`system-dependencies` for optional tools that make an agent more capable.

.. note::

    We are working on a graphical way to create and interact with agents using
    the :ref:`gptme web interface <server:gptme-webui>`. Try it out and let us
    know what you think! Soon coming as a managed service.

How it works
------------

- **The workspace is the agent.** Identity, journal, tasks, knowledge, lessons,
  and history live in a git repository you own. See :doc:`agents/workspace`.
- **Runs are scheduled.** A service manager triggers gptme in that workspace on
  an interval you choose. See :doc:`agents/autonomous`.
- **It learns by writing things down.** Each run updates the journal, tasks, and
  :doc:`lessons`; committing them is what makes the next run better than the last.

It reaches the rest of the world through the same surfaces as any gptme session:
:doc:`channels` for email, chat, and voice; :doc:`tools` for browsing, search,
and the shell; and :doc:`automation` for CI and git workflows.

Read more:

- :doc:`agents/workspace` — the repository that holds identity, tasks, journal,
  knowledge, and lessons, and how context is built each run.
- :doc:`agents/autonomous` — scheduling runs, watching them, prompts that hold up
  unattended, and keeping the blast radius small.
- :doc:`profiles` — named presets of system prompt, tool access, and behavior,
  for restricted sessions and subagents.

.. note::

    An agent is not the only way to specialize gptme. A
    :doc:`profile <profiles>` is the lighter-weight sibling: a named preset of
    system prompt, tool access, and behavior rules for a restricted session or a
    subagent with a clear role, with no workspace of its own.

    Compared to assistants built around messaging gateways (see
    :doc:`alternatives`), a gptme agent is workspace-first: its identity, memory,
    and history live in files you own, and it reaches people through whichever
    :doc:`channels <channels>` you connect.

Why personify agents?
---------------------

While personifying agents might seem unnecessary for professional use, it provides several benefits:

- **Mental Model:** Helps users understand the agent's role and capabilities
- **Consistency:** Encourages consistent interaction patterns and expectations
- **Memory:** Makes it easier to remember what you've told the agent
- **Engagement:** Creates more natural and memorable interactions
- **Identity:** Distinguishes between different specialized agents

Examples
--------

`Bob <https://github.com/TimeToBuildBob>`__ (``@TimeToBuildBob``) is an
experimental agent that helps with gptme development: project management and task
tracking, code review and development assistance, documentation and knowledge
management (he has a `website <https://timetobuildbob.github.io/>`_), and
community interaction (he reads and responds on the Discord server). He tries to
be more than an AI assistant — expanding his own impact and seeking autonomy to
safely scale his efforts and improve the agent harness.

How Bob selects work, prioritizes it, and drives models is deliberately not
documented here: it is one working solution to open questions, and the parts that
prove general are upstreamed into gptme and the template as they mature.

The same template works for other domains: development assistants with
project-specific knowledge, research assistants with domain expertise, personal
productivity assistants with custom workflows, and team collaboration agents with
shared knowledge bases.

Links
-----

For more details, see the following resources:

- `gptme-agent-template <https://github.com/gptme/gptme-agent-template/>`_ - Template for creating new agents
- `gptme-contrib <https://github.com/gptme/gptme-contrib>`_ - Community-contributed tools and scripts for agents

.. toctree::
   :hidden:

   agents/workspace
   agents/autonomous
   Profiles <profiles>
