Agents
======

gptme supports highly customizable "agents": persistent AI assistants with structured memory, identity, and workspace management capabilities.

Each agent is implemented as a git repository that serves as their "brain," containing all their data, configuration, and interaction history.

Architecture
------------

**Git-based Repository Structure:** Each agent is a complete git repository with a structured workspace.

- **Core files** - ``README.md``, ``ABOUT.md``, ``ARCHITECTURE.md``, ``gptme.toml``
- ``journal/`` - Daily activity logs (YYYY-MM-DD.md format)
- ``tasks/`` - Individual task files with YAML metadata
- ``knowledge/`` - Long-term documentation and insights
- ``people/`` - Contact profiles and relationship management
- ``projects/`` - Project-specific information

**Dynamic Context Generation:** Agents use sophisticated context generation to maintain awareness.

- :doc:`Project configuration <config>` (``gptme.toml``) specifies core files always in context
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

**People Directory:**

- Individual profiles for contacts and collaborators
- Includes interests, skills, project history, and interaction notes
- Privacy-conscious with appropriate detail levels

Usage
-----

**Creating an Agent:**

Use the `gptme-agent-template <https://github.com/gptme/gptme-agent-template/>`_ to create new agents:

.. code-block:: bash

    # Clone the template repository
    git clone https://github.com/gptme/gptme-agent-template
    cd gptme-agent-template

    # Fork the template
    ./fork.sh ../my-agent "MyAgent"
    cd ../my-agent

**Running an Agent:**

.. code-block:: bash

    # Install dependencies
    pipx install gptme
    pipx install pre-commit
    make install

    # Run the agent
    ./run.sh "your prompt here"

**Execution Flow:**

1. ``run.sh`` builds dynamic context from all agent systems
2. Context includes journal entries, task status, and current git state
3. ``gptme`` runs with comprehensive situational awareness
4. Agent can access and update all workspace components

Benefits
--------

**Version Control:**

- All agent data and interactions are version-controlled
- Complete history of agent development and interactions
- Easy backup, sharing, and collaboration

**Persistence:**

- Agents maintain state across sessions
- Remember previous conversations, decisions, and progress
- Build knowledge and relationships over time

**Structured Memory:**

- Organized information storage prevents knowledge loss
- Easy retrieval of past decisions and context
- Cross-referencing between different information types

**Extensibility:**

- Template provides consistent foundation
- Customizable identity, goals, and capabilities
- Integration with external tools and services

**Goal-Oriented Behavior:**

- Clear goals transform agents from reactive tools into proactive collaborators
- Well-defined purpose enables agents to take initiative, suggest improvements, and identify opportunities
- Strategic direction helps agents prioritize decisions and maintain long-term perspective
- Goals provide the contextual framework that "pulls agents forward" toward meaningful outcomes

Examples
--------

**Bob:**
Bob, aka `@TimeToBuildBob <https://github.com/TimeToBuildBob>`_, is an experimental agent that helps with gptme development. He demonstrates practical agent capabilities including:

- Project management and task tracking
- Code review and development assistance
- Documentation and knowledge management
- Community interaction and support

**Creating Specialized Agents:**
The template system enables creating agents for specific domains:

- Development assistants with project-specific knowledge
- Research assistants with domain expertise
- Personal productivity assistants with custom workflows
- Team collaboration agents with shared knowledge bases

External Integrations
---------------------

Agents can be extended with various external integrations and tools for enhanced capabilities:

**Content & Information:**

- **Web Browsing:** Access and analyze web content using built-in browser tools
- **Search Integration:** Query search engines and process results
- **RSS Reader:** Consume and process RSS feeds in LLM-friendly formats

**Communication & Sharing:**

- **Email Integration:** Send and receive emails for external communication
- **Social Media:**

  - Twitter integration for sharing updates and public communication
  - Discord integration for community interaction

- **GitHub Integration:** Create and share gists, manage repositories
- **Website Publishing:** Share information and updates publicly

**Collaboration Tools:**

- **Git Integration:** Version control with co-authoring capabilities
- **Issue Tracking:** Integration with GitHub issues and project management
- **Documentation:** Automated documentation generation and updates

**Development & Operations:**

- **CI/CD Integration:** Automated testing and deployment workflows
- **Monitoring:** System and application monitoring capabilities
- **Database Access:** Query and update databases as needed

These integrations transform agents from isolated assistants into connected participants in digital workflows, enabling them to:

- Stay informed about relevant developments through content feeds
- Communicate with external parties and communities
- Share their work and insights publicly
- Collaborate on projects with proper attribution
- Maintain awareness of project status and issues

**Note:** Many integrations are work-in-progress (WIP) and under active development.

Why personify agents?
---------------------

While personifying agents might seem unnecessary for professional use, it provides several benefits:

- **Mental Model:** Helps users understand the agent's role and capabilities
- **Consistency:** Encourages consistent interaction patterns and expectations
- **Memory:** Makes it easier to remember what you've told the agent
- **Engagement:** Creates more natural and memorable interactions
- **Identity:** Distinguishes between different specialized agents

Links
-----

For more details, see the following resources:

- `gptme-agent-template <https://github.com/gptme/gptme-agent-template/>`_ - Template for creating new agents
- `gptme-contrib <https://github.com/gptme/gptme-contrib>`_ - Community-contributed tools and scripts for agents
