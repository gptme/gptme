"""Unified ``gptme-util review`` command group.

Groups review-related subcommands under a single ``review`` namespace:

    gptme-util review watch 1234     # poll a PR for feedback and iterate fixes
    gptme-util review pr 1234        # (future) run an AI review pass on a PR

See gptme#3442 for the convergence design between pr_review (gptme-contrib)
and review-watch.

Backward compatibility: ``gptme-util review-watch`` continues to work as a
top-level alias so existing scripts are not broken.
"""

from __future__ import annotations

import click


@click.group("review")
def review() -> None:
    """Review pipeline commands.

    \b
    Subcommands:
        watch   Poll a PR for review feedback and iterate fixes automatically.
    """


# Attach the ``watch`` subcommand from cmd_review_watch, renamed for the group.
# The import runs at module initialization (via the add_command call below);
# the watch module currently has no heavy import-time dependencies.
def _get_watch_command() -> click.Command:
    from .cmd_review_watch import review_watch

    # Clone the command with the name "watch" so it appears as
    # ``gptme-util review watch`` in help output.
    cmd = click.Command(
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
    return cmd


# Register the watch command at import time so ``gptme-util review --help``
# lists it without needing to invoke the subcommand first.
review.add_command(_get_watch_command())
