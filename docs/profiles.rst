:audience: power-user

Agent Profiles
==============

A profile is a named preset for an agent. It combines:

- a **system prompt** addition that sets the agent's role and focus,
- a **tool allowlist** that limits which tools are loaded, and
- **behavior rules** such as read-only or no network access.

Use a profile to run a restricted session, or to give a :doc:`subagent <tools/subagent>` a
clear role.

Using a profile
---------------

Start a session with a profile, and list or inspect the available profiles:

.. code-block:: bash

    gptme --agent-profile explorer "summarize how authentication works in this repo"

    gptme-util profile list
    gptme-util profile show explorer

Subagents take a profile too:

.. code-block:: python

    subagent("reviewer", "Review the PR diff for correctness issues", profile="verifier")

Tool restrictions are hard-enforced in the CLI (``--agent-profile``) and for
subagents in thread mode: only the allowed tools are loaded, so the model can't
call the others. Behavior rules such as ``read_only`` and ``no_network`` are soft —
they're expressed in the system prompt, not enforced at the tool level.

Built-in profiles
-----------------

.. list-table::
   :header-rows: 1
   :widths: 15 30 20 35

   * - Profile
     - Tools
     - Behavior
     - Use case
   * - ``default``
     - All
     -
     - The standard gptme experience
   * - ``developer``
     - All
     -
     - Full development capabilities
   * - ``explorer``
     - ``read``, ``chats``
     - Read-only, no network
     - Read-only exploration of a codebase
   * - ``researcher``
     - ``browser``, ``read``, ``screenshot``, ``chats``
     - Read-only
     - Web research without modifying local files
   * - ``verifier``
     - ``read``, ``ipython``, ``shell``, ``chats``
     - Read-only, no network
     - Review, test, and verify work done by other agents
   * - ``isolated``
     - ``read``, ``ipython``
     - Read-only, no network
     - Processing potentially untrusted content
   * - ``computer-use``
     - ``computer``, ``browser``, ``vision``, ``ipython``, ``shell``
     -
     - UI automation: structured browser interaction for web, screenshots for native apps
   * - ``browser-use``
     - ``browser``, ``screenshot``, ``vision``, ``ipython``, ``shell``
     -
     - Web browsing and testing

Run ``gptme-util profile show <name>`` to see a profile's full system prompt.

Profiles for subagents
----------------------

A subagent's profile is resolved in this order:

1. An explicit ``profile=`` argument (highest priority).
2. The profile for a ``role=`` argument, e.g. ``role="verify"`` → ``verifier``.
3. Auto-detection from the ``agent_id``, when it matches a profile name or alias
   (e.g. ``"verify"`` → ``verifier``).
4. No profile: the subagent keeps the default tools.

So naming a subagent after a profile is enough to apply it:

.. code-block:: python

    # Desktop interaction (mouse, keyboard, screenshots)
    subagent("computer-use", "Click the Submit button and screenshot the result")

    # Typed delegation: verifier profile, run in a subprocess with a throwaway worktree
    subagent("my-reviewer", "Check that the refactor passes all tests", role="verify")

``role="verify"`` also sets ``use_subprocess=True`` and ``isolated=True`` by default,
so the verifier can't accidentally modify the parent workspace; explicit
``use_subprocess`` and ``isolated`` arguments override these defaults. The
verifier's ``read_only`` rule remains a soft, prompt-level rule.

Custom profiles
---------------

Define your own profiles in ``~/.config/gptme/profiles/`` as Markdown or TOML files.
If both define the same name, the Markdown file wins.

In a Markdown profile, YAML front matter holds the metadata and the body becomes
the system prompt:

.. code-block:: markdown

    ---
    name: reviewer
    description: Careful code reviewer
    tools:
      - read
      - shell
    behavior:
      read_only: true
    ---

    You are a careful code reviewer. Focus on correctness and test coverage.

A TOML profile uses the same fields, with ``system_prompt`` as a key:

.. code-block:: toml

    name = "reviewer"
    description = "Careful code reviewer"
    system_prompt = "You are a careful code reviewer. Focus on correctness and test coverage."
    tools = ["read", "shell"]

    [behavior]
    read_only = true

``name`` and ``description`` are required. Omit ``tools`` to allow all tools; an
empty list allows none.
