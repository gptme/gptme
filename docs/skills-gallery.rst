Skills Gallery
==============

A curated selection of community skills from the default
`gptme-contrib <https://github.com/gptme/gptme-contrib>`_ registry. These are
hand-picked, battle-tested workflows — several run in production agent ops —
and each one installs with the same ``gptme-util skills install`` command below.

Installing
----------

Skills from ``gptme-contrib`` install with ``gptme-util skills install``. That
copies the skill into the primary skills directory (see ``gptme-util skills dirs``),
which gptme already scans (see :doc:`skills`). The command is the same for every
skill on this page — only the skill name changes:

.. code-block:: bash

   gptme-util skills install home-assistant

After install, skills auto-load when their name appears in a message — mention
"home-assistant" and gptme pulls that skill into context.

If you already have ``gptme-contrib`` cloned, install from the local path directly — the
same ``install`` command accepts a directory path:

.. code-block:: bash

   gptme-util skills install /absolute/path/to/gptme-contrib/skills/home-assistant

Do not add a relative ``gptme-contrib/skills`` path to ``[lessons] dirs``.
That option is for lesson trees, and a relative path there is resolved against
the current working directory, not the clone location.

The curated list
----------------

.. list-table::
   :header-rows: 1
   :widths: 18 30 20 32

   * - Skill
     - What it does
     - Social proof
     - Source
   * - **home-assistant**
     - Query a Home Assistant instance for presence, sensor data, calendar
       events, and cameras.
     - Included in the default contrib registry; used in live agent ops.
     - ``gptme-contrib/skills/home-assistant``
   * - **gptme-wrapped**
     - Analyze your gptme conversation history for insights like token usage,
       costs, model preferences, and usage patterns.
     - Community-facing showcase skill.
     - ``gptme-contrib/skills/gptme-wrapped``
   * - **code-review-helper**
     - Systematic, multi-lens code review workflow with bundled utilities for
       structured, pattern-aware feedback.
     - Proven in production PR review loops.
     - ``gptme-contrib/skills/code-review-helper``
   * - **agentic-presentation**
     - Generate an offline, single-file HTML presentation from structured
       slide JSON.
     - Clean, self-contained output artifact.
     - ``gptme-contrib/skills/agentic-presentation``
   * - **artifact-publishing**
     - Publish any HTML artifact (demo, visualization, interactive content) to
       a static host such as GitHub Pages.
     - Used for real publishing in production agent stacks.
     - ``gptme-contrib/skills/artifact-publishing``

For the complete inventory of skills and lessons, see the
`gptme-contrib skills directory <https://github.com/gptme/gptme-contrib/tree/master/skills>`_.

Why curated?
------------

gptme skills are powerful but filesystem-scanned — discoverable only if you
know where to look. This gallery is the human-facing discovery surface that
points you at the right skill to reach for. It is intentionally small and
hand-picked rather than an exhaustive registry, so each entry carries a clear
pitch and social proof.

Contributing
------------

Have a skill that should be here? Add it to `gptme-contrib
<https://github.com/gptme/gptme-contrib>`_ and tag it with the ``gptme-skill``
topic so it is discoverable; community skills with real star/fork counts can be
added to this gallery alongside the curated defaults.

Related
-------

- :doc:`skills` - the full skills system, formats, and loading paths
- :doc:`lessons` - the knowledge system skills extend
- `gptme-contrib README <https://github.com/gptme/gptme-contrib>`_ - the registry this gallery curates
