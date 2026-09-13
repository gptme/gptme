:audience: power-user

Running autonomously
====================

An agent becomes useful when it runs without you. This page covers scheduling
runs, keeping an eye on them, writing prompts that hold up unattended, and
containing what a run can break.

Scheduling runs
---------------

``gptme-agent install`` sets up a systemd service and timer (Linux) or a launchd
plist (macOS) that runs the agent in its workspace on a schedule:

.. code-block:: bash

    gptme-agent install                          # every 30 minutes (default)
    gptme-agent install --schedule "*:00"        # every hour
    gptme-agent install --schedule "*-*-* 06:00" # daily at 06:00
    gptme-agent install --schedule "Mon *:00"    # hourly on Mondays

Schedules use the systemd ``OnCalendar`` format on both platforms; the launchd
plist is generated from it. Use ``--name`` and ``--workspace`` to manage an agent
other than the one in the current directory.

Scheduling comes in two shapes, which are less opposed than they sound:

- **Timer-based** — ``gptme-agent install`` wakes the agent on a cadence, whether
  or not anything happened since the last run.
- **Event-driven** — the agent acts when something happens: an issue is filed, a
  PR gets a comment, CI fails, mail arrives, an alert fires.
- **A timer approximates event-driven** when each run starts by checking those
  event streams, which is how most agents end up working in practice.

Beyond a plain timer
--------------------

``gptme-agent install`` gives you the timer. What the agent does when it wakes is
a separate question, and in practice two loop shapes show up:

- **Autonomous runs** start on a cadence without a specific trigger. The run
  itself decides what to work on, given the workspace state: what's in the task
  queue, what was left unfinished, what the journal says happened last time.
- **Project-monitoring runs** react to something that happened: an email
  arrives, an issue or PR gets activity, CI turns green or red, a PR goes
  conflicted after something else merged.

The `gptme-runloops <https://github.com/gptme/gptme-contrib/tree/master/packages/gptme-runloops>`__
package in gptme-contrib implements both, plus email and multi-agent
coordination loops:

.. code-block:: bash

    gptme-runloops autonomous --workspace ~/my-agent   # cadence-driven run
    gptme-runloops monitoring --workspace ~/my-agent   # react to GitHub activity
    gptme-runloops email --workspace ~/my-agent        # react to incoming mail

How a run picks its work — which task, which model, which harness — is the open
part. `Bob <https://github.com/TimeToBuildBob>`__ samples that choice at the
start of each autonomous run rather than fixing it in the schedule; that is one
working approach, not a prescription, and pieces of it are being upstreamed as
they prove out.

Watching and controlling
------------------------

.. code-block:: bash

    gptme-agent status            # is it installed, when did it last run
    gptme-agent logs -f           # follow a run (-n 100 for more history)
    gptme-agent run               # trigger a run now, without waiting
    gptme-agent stop              # pause scheduled runs
    gptme-agent start             # resume them
    gptme-agent restart           # restart services
    gptme-agent list              # every installed agent on this host
    gptme-agent uninstall         # remove the services

``gptme-agent scan`` is a different question: it inspects *processes* running
right now (gptme, claude-code, codex, aider, …), not installed agents or
workspaces.

When something looks wrong, ``gptme-agent doctor`` checks the workspace itself —
identity files, ``gptme.toml``, directory structure, git setup, pre-commit hooks,
and tools such as ``uv`` and ``gh``. ``--fix`` repairs the simple problems.

.. code-block:: bash

    gptme-agent doctor            # check the current workspace
    gptme-agent doctor ~/my-agent --fix

Prompts that survive unattended runs
------------------------------------

Nobody is watching to answer a question or approve a step, so the prompt has to
carry the judgment:

- **Name the stopping point.** Ask for work that ends at something reviewable — a
  branch, a PR, a draft, a written report — rather than an action that lands
  outside the workspace. "Show me the diff, don't push" is a boundary; "improve
  the repo" is not.
- **Say what to do when it's ambiguous.** Tell it to file a task or write a
  journal note instead of guessing, so an unclear situation produces a question
  you can answer later rather than a wrong commit.
- **Pick work from a queue, not the whole world.** An agent that selects its next
  task from its own backlog stays focused; one pointed at every notification
  thrashes.
- **Leave a trail.** Have it update the journal and tasks as it goes, so the next
  run has context and you can reconstruct what happened.

Keeping an agent doing good work in a loop, day and night, is an open problem,
and a structural one rather than a scheduling detail: which events and tasks it
can see, what it picks up next, when it stops, what it spends, and how work is
queued — all against the inference and models available to it.
`Bob <https://github.com/TimeToBuildBob>`__ is one working solution that does
this its own way; the parts that prove general are upstreamed into gptme and the
template as they mature.

For scheduling that isn't an agent — one-off scripts, CI jobs, cron pipelines —
see :doc:`../automation`.

Containing the blast radius
---------------------------

An unattended agent does whatever its prompt, tools, and credentials permit, and
confirmations aren't there to catch mistakes. Before you let one run on a
schedule, decide what it could reach on its worst run — see :doc:`../security`
for the full picture, which in short means:

- **Give it its own environment.** A container, VM, or dedicated machine with
  only the repositories and credentials it needs beats a laptop holding your SSH
  keys, cloud credentials, and logged-in sessions.
- **Scope its credentials.** A dedicated account with fine-grained tokens limits
  what a bad run can touch, and makes its actions auditable.
- **Keep work in version control.** Changes that land as commits on a branch are
  reviewable and reversible; changes that land anywhere else are not.
- **Keep a human at irreversible boundaries.** Merging, deploying, publishing,
  spending money, and sending messages deserve review even when everything before
  them was autonomous.
- **Read the logs early on.** ``gptme-agent logs -f`` over the first few runs
  tells you whether the prompt and guardrails actually hold.
