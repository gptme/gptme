"""Unified ``gptme-util review`` command group.

Groups review-related subcommands under a single ``review`` namespace:

    gptme-util review pr 1234        # run an AI review pass → ReviewArtifact
    gptme-util review watch 1234     # poll a PR for feedback and iterate fixes

Full pipeline example::

    # Stage 1: AI reviewer reads diff, produces structured findings
    gptme-util review pr 1234 --repo owner/repo --save artifact.json

    # Stage 2: AI author reads findings, iterates fixes until PR is approved
    gptme-util review watch --artifact artifact.json

See gptme#3442 for the convergence design between pr_review (gptme-contrib)
and review-watch.

Backward compatibility: ``gptme-util review-watch`` continues to work as a
top-level alias so existing scripts are not broken.
"""

from __future__ import annotations

import click


@click.group("review")
def review() -> None:
    """Unified review pipeline: AI reviewer + AI author fix loop.

    \b
    Subcommands:
        pr      Run an AI review pass on a PR → ReviewArtifact JSON.
        watch   Poll a PR for review feedback and iterate fixes automatically.

    \b
    Full pipeline:
        gptme-util review pr 1234 --save artifact.json
        gptme-util review watch --artifact artifact.json
    """


# ---------------------------------------------------------------------------
# Attach subcommands
# ---------------------------------------------------------------------------


def _get_watch_command() -> click.Command:
    """Return the ``watch`` subcommand, cloned from cmd_review_watch."""
    from .cmd_review_watch import review_watch

    # Clone the command with the name "watch" so it appears as
    # ``gptme-util review watch`` in help output while the top-level
    # ``gptme-util review-watch`` alias continues to work unchanged.
    return click.Command(
        name="watch",
        callback=review_watch.callback,
        params=review_watch.params,
        help=review_watch.help,
        epilog=review_watch.epilog,
        short_help=review_watch.short_help,
        add_help_option=review_watch.add_help_option,
        no_args_is_help=review_watch.no_args_is_help,
        hidden=review_watch.hidden,
        deprecated=review_watch.deprecated,
    )


def _get_pr_command() -> click.Command:
    """Return the ``pr`` subcommand from cmd_review_pr."""
    from .cmd_review_pr import review_pr

    return review_pr


# Register both subcommands at import time so ``gptme-util review --help``
# lists them without needing to invoke a subcommand first.
review.add_command(_get_watch_command())
review.add_command(_get_pr_command())
