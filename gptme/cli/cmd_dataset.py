"""CLI for gptme dataset construction from session trajectories.

Commands::

    gptme-util dataset stats       # corpus statistics
    gptme-util dataset export      # export TaskEnvironment JSONL

See ``gptme.dataset.trajectory_to_env`` for implementation details and
``gptme/gptme#3718`` for the motivating issue.
"""

import json
import logging
import sys
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.group()
def dataset() -> None:
    """Build fine-tuning datasets from gptme session trajectories.

    Uses commit-message session-ID attribution to recover the git state
    before and after each session, producing TaskEnvironment JSONL records
    suitable for fine-tuning pipelines.

    References:
      Terminal-Universe paper: https://arxiv.org/abs/2609.04148
      Issue: gptme/gptme#3718
    """


@dataset.command("stats")
@click.option(
    "--repo",
    "-r",
    "repo_path",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Git repository to mine for session-attributed commits.",
)
@click.option(
    "--logs-dir",
    "logs_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Override gptme logs directory.",
)
@click.option(
    "--limit",
    "-n",
    default=500,
    show_default=True,
    help="Maximum number of sessions to scan.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def stats_cmd(
    repo_path: Path,
    logs_dir: Path | None,
    limit: int,
    as_json: bool,
) -> None:
    """Print corpus statistics: how many sessions are convertible into environments."""
    from ..dataset.trajectory_to_env import corpus_stats

    try:
        result = corpus_stats(
            repo_path=repo_path.resolve(),
            logs_dir=logs_dir,
            limit=limit,
        )
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return

    click.echo(f"Sessions scanned:   {result['scanned']}")
    click.echo(f"Convertible:        {result['convertible']}")
    click.echo(f"Yield:              {result['yield_pct']}%")
    if result["category_counts"]:
        click.echo("Categories:")
        for cat, n in sorted(result["category_counts"].items(), key=lambda x: -x[1]):
            click.echo(f"  {cat:<12} {n}")
    if result["model_counts"]:
        click.echo("Models:")
        for model, n in sorted(result["model_counts"].items(), key=lambda x: -x[1]):
            click.echo(f"  {model:<40} {n}")


@dataset.command("export")
@click.option(
    "--repo",
    "-r",
    "repo_path",
    default=".",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Git repository to mine for session-attributed commits.",
)
@click.option(
    "--logs-dir",
    "logs_dir",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Override gptme logs directory.",
)
@click.option(
    "--limit",
    "-n",
    default=None,
    type=int,
    help="Maximum number of sessions to scan.",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default="-",
    help="Output file path (default: stdout, use '-' for stdout).",
)
@click.option(
    "--min-commits",
    default=1,
    show_default=True,
    help="Minimum solution commits required to include an environment.",
)
@click.option(
    "--include-test",
    is_flag=True,
    help="Include test/eval conversation IDs (excluded by default).",
)
def export_cmd(
    repo_path: Path,
    logs_dir: Path | None,
    limit: int | None,
    output_path: str,
    min_commits: int,
    include_test: bool,
) -> None:
    """Export TaskEnvironment records as JSONL.

    Each line of output is a JSON object describing one reproducible training
    environment derived from a gptme session.  The schema matches the
    ``TaskEnvironment`` dataclass in ``gptme.dataset.trajectory_to_env``.

    Example::

      gptme-util dataset export --repo /path/to/repo -n 200 -o envs.jsonl
    """
    from ..dataset.trajectory_to_env import extract_environments

    try:
        repo = repo_path.resolve()
        env_iter = extract_environments(
            repo_path=repo,
            logs_dir=logs_dir,
            limit=limit,
            include_test=include_test,
            min_commits=min_commits,
        )
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    out = sys.stdout if output_path == "-" else open(output_path, "w", encoding="utf-8")
    count = 0
    try:
        for env in env_iter:
            out.write(env.to_jsonl() + "\n")
            count += 1
    finally:
        if output_path != "-":
            out.close()

    click.echo(f"Exported {count} environments.", err=True)
