"""CLI commands for the gptme anti-slop quality gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..anti_slop import DEFAULT_MODE, MODES, _format_report, evaluate_gate


@click.group()
def anti_slop() -> None:
    """Detect LLM-generated slop in text (hedging, tics, em-dash abuse, …)."""


@anti_slop.command("check")
@click.argument(
    "file",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--text", "inline_text", default=None, help="Inline text to scan instead of a file."
)
@click.option(
    "--mode",
    type=click.Choice(list(MODES)),
    default=DEFAULT_MODE,
    show_default=True,
    help=(
        "Sensitivity preset. "
        "relaxed=heavy em-dash writer/personal blog, "
        "balanced=general technical writing, "
        "strict=suspected AI output."
    ),
)
@click.option(
    "--warn-threshold",
    type=float,
    default=None,
    help="Override warn score from the preset.",
)
@click.option(
    "--fail-threshold",
    type=float,
    default=None,
    help="Override fail score from the preset.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the full JSON report.")
@click.option(
    "--top",
    type=int,
    default=5,
    show_default=True,
    help="Number of top smells to show.",
)
@click.option(
    "--no-fail", is_flag=True, help="Always exit 0; useful for baseline collection."
)
def check(
    file: Path | None,
    inline_text: str | None,
    mode: str,
    warn_threshold: float | None,
    fail_threshold: float | None,
    as_json: bool,
    top: int,
    no_fail: bool,
) -> None:
    """Check a file (or stdin) for LLM-slop patterns and print a gate report.

    Exit codes: 0=pass/warn, 1=fail (unless --no-fail).

    Examples:

    \b
        gptme-anti-slop check draft.md
        gptme-anti-slop check draft.md --mode strict
        cat output.md | gptme-anti-slop check --json
    """
    if file is not None and inline_text is not None:
        raise click.UsageError("Pass either FILE or --text, not both.")

    if inline_text is not None:
        text = inline_text
    elif file is not None:
        text = file.read_text(encoding="utf-8", errors="replace")
    else:
        if sys.stdin.isatty():
            raise click.UsageError("Pass a FILE argument or pipe text to stdin.")
        text = sys.stdin.read()

    report = evaluate_gate(
        text,
        mode=mode,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
    )

    if as_json:
        click.echo(json.dumps(report, indent=2))
    else:
        click.echo(_format_report(report, top=top))

    if not no_fail and report["status"] == "fail":
        raise SystemExit(1)


def main() -> None:
    """Entry point for the standalone gptme-anti-slop script."""
    anti_slop()


if __name__ == "__main__":
    main()
