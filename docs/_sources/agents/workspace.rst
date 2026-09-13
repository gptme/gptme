:audience: power-user

Workspace
=========

An agent *is* its workspace: a git repository holding identity, memory, tasks,
and history. This page describes what lives there, how context is assembled each
run, and why a plain git repo is the right shape for it.

Overview
--------

✨ Superpowers
^^^^^^^^^^^^^^

.. mermaid::

   graph LR
       Persistent[🔒 Persistent<br/>Complete history<br/>Version controlled]
       Autonomous[🎯 Autonomous<br/>Long-term goals<br/>Proactive & self-directed]
       Evolving[🌱 Self-Improving<br/>Gets smarter over time<br/>Learns from experience]
       Portable[🔁 Portable<br/>Workspace-owned identity<br/>Multiple runtimes]

       %% Force left-to-right layout
       Persistent --- Autonomous --- Evolving --- Portable

       classDef benefits fill:#fff8e1,stroke:#f57f17,stroke-width:3px,color:#000
       class Persistent,Autonomous,Evolving,Portable benefits

.. _agent-runtime-portability:

🔁 Runtime Portability
^^^^^^^^^^^^^^^^^^^^^^

**Your agent is the workspace, not the harness.** Identity, memory, tasks,
lessons, journal, workflow, and audit history live in the version-controlled
workspace. gptme is the native runtime for that workspace, but another harness
can operate on the same durable agent state when it implements the same context
and task-lifecycle contract.

Keep four layers distinct:

- **Agent workspace** — identity, memory, tasks, journal, lessons, and workflow.
- **Harness / runtime** — the agent loop, tools, context loading, and permissions.
- **Model / provider** — the model used for reasoning and generation.
- **Access / billing** — API keys, local inference, managed services, or a
  compatible subscription login.

These layers are not a full Cartesian product. Each harness supports different
models and access methods, and merely being able to read the repository is not
the same as shipping a reliable autonomous adapter. See the
`gptme-agent-template runtime compatibility matrix
<https://github.com/gptme/gptme-agent-template#one-agent-multiple-runtimes>`_
for the concrete support levels in the public template.

🧠 Agent Brain
^^^^^^^^^^^^^^

.. mermaid::

   graph TD
       subgraph Core[💎 Core Identity]
           Identity[Who am I?<br/>My goals & capabilities]
       end

       subgraph LivingMemory[🔄 Living Memory Systems]
           Journal[📔 Journal<br/>Every decision & insight<br/>Continuous learning]
           Tasks[🎯 Tasks<br/>Goals & achievements<br/>Progress tracking]
           Knowledge[📚 Knowledge<br/>Learned lessons<br/>Cross-referenced insights]
           People[👥 Relationships<br/>Collaboration history<br/>Social intelligence]
           Projects[🚀 Projects<br/>Active work & outcomes<br/>Success patterns]
       end

       subgraph Intelligence[🤖 Dynamic Intelligence]
           direction LR
           Context[⚡ Live Context<br/>Situational awareness<br/>Current state]
           Learning[📈 Continuous Learning<br/>Self-improvement<br/>Pattern recognition]
       end

       %% Internal intelligence flow
       Core --> LivingMemory
       LivingMemory --> Intelligence
       Context --- Learning

       %% Memory interconnections (selective)
       Journal -.->|Informs| Tasks
       Knowledge -.->|Supports| Projects
       People -.->|Collaborate on| Projects

       classDef core fill:#fff3e0,stroke:#ef6c00,stroke-width:3px,color:#000
       classDef memory fill:#e8f5e8,stroke:#2e7d32,stroke-width:2px,color:#000
       classDef intelligence fill:#fce4ec,stroke:#c2185b,stroke-width:3px,color:#000

       class Core,Identity core
       class LivingMemory,Journal,Tasks,Knowledge,People,Projects memory
       class Intelligence,Context,Learning intelligence

🌍 External World
^^^^^^^^^^^^^^^^^

.. mermaid::

   graph LR
       subgraph World
           User[👤 User]
           Web[🌐 Web & APIs]
           Files[📁 Files & Code]
           Social[✉️ Email & Discord]
       end

       classDef world fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#000

       class World,User,Web,Files,Social world

Architecture
------------

**Git-based Repository Structure:** Each agent is a complete git repository with a structured workspace.

- **Core files** - ``README.md``, ``ABOUT.md``, ``ARCHITECTURE.md``, ``gptme.toml``
- ``journal/`` - Daily activity logs (YYYY-MM-DD.md format)
- ``tasks/`` - Individual task files with YAML metadata
- ``knowledge/`` - Long-term documentation and insights
- ``lessons/`` - Learned lessons and best practices
- ``people/`` - Contact profiles and relationship management
- ``projects/`` - Project-specific information

**Dynamic Context Generation:** Agents use sophisticated context generation to maintain awareness.

- :doc:`Project configuration <../config>` (``gptme.toml``) specifies core ``files`` always in context
- A ``context_cmd`` command specified in ``gptme.toml`` is used for dynamic context generation
- Each interaction includes recent journal entries, active tasks, and git status
- Provides comprehensive situational awareness across sessions

Key Systems
-----------

**Journal System:**

- One file per day in append-only format
- Contains task progress, decisions, reflections, and plans
- Most recent entries automatically included in context
- Maintains historical record of all activities and thoughts

**Task Management:**

- Individual Markdown files with YAML frontmatter metadata
- States: new, active, paused, done, cancelled
- Priority levels, tags, and dependencies
- CLI tools for management and status tracking
- Integrated with journal entries for progress updates

**Knowledge Base:**

- Long-term information storage organized by topic
- Technical documentation, best practices, and insights
- Cross-referenced with tasks and journal entries

**Lessons System:**

- Used to document learned lessons and best practices
- Learned lessons are to be retrieved when the context arises
- Helps avoid repeating mistakes and improves decision-making

**People Directory:**

- Individual profiles for contacts and collaborators
- Includes interests, skills, project history, and interaction notes
- Privacy-conscious with appropriate detail levels

What creation gave you
----------------------

``gptme-agent create`` clones the
`gptme-agent-template <https://github.com/gptme/gptme-agent-template/>`_ and
customizes it for your agent: identity files, knowledge and lesson directories,
and automation scaffolding. Pass ``--no-template`` for a bare directory layout
instead, or clone the template yourself and run ``./scripts/fork.sh`` — the
command just automates that.

Run the agent from its workspace, as you would any gptme session:

.. code-block:: bash

    cd ~/my-agent
    gptme "your prompt here"

Everything it learns lands in the workspace: journal entries, tasks, knowledge,
and :doc:`lessons <../lessons>`. Commit them — the git history *is* the agent's
memory, and what makes the next session better than the last.

What happens each run
---------------------

1. ``gptme`` builds context from all systems

   - Includes journal entries, tasks, knowledge, and people
   - Static context is included using the ``files`` in ``gptme.toml``
   - Dynamic context is generated using the ``context_cmd`` in ``gptme.toml``

2. ``gptme`` runs the agent

   - With prompt, tools, and collected context

3. Agent processes the prompt

   - Uses the context to inform decisions and responses
   - Updates journal, tasks, and knowledge as needed

Why a git repo
--------------

Using an ordinary repository as the agent's brain buys three things:

- **A complete history.** Every decision, note, and change is version-controlled,
  so you can review what the agent did, revert it, back it up, or hand the
  workspace to someone else.
- **State that survives the session.** The agent remembers previous
  conversations, decisions, and progress, and builds knowledge and relationships
  over time instead of starting cold.
- **Memory you can navigate.** Structured directories keep information findable —
  past decisions, cross-references between tasks and knowledge — rather than
  buried in chat logs.

The template gives every agent the same foundation, while identity, goals, and
capabilities stay yours to customize.
