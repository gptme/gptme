"""
CLI for running Terminal-Bench evaluations with gptme.

This provides a convenient wrapper around terminal-bench's `tb run` command,
pre-configured to use the gptme agent adapter.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

import click

from . import DEFAULT_MODEL


@click.command()
@click.option(
    "--model",
    "-m",
    default=DEFAULT_MODEL,
    show_default=True,
    help="Model to use.",
)
@click.option(
    "--dataset",
    default="terminal-bench-core==head",
    show_default=True,
    help="Terminal-Bench dataset. Pin to a specific version (e.g. terminal-bench-core==1.0.0) for reproducible benchmark comparisons.",
)
@click.option("--task", "-t", multiple=True, help="Task ID(s) to run. Omit to run all.")
@click.option(
    "--n-trials", default=1, show_default=True, help="Number of trials per task."
)
@click.option(
    "--output-dir",
    default="runs/tbench",
    show_default=True,
    help="Directory for results.",
)
@click.option("--verbose", "-v", is_flag=True, help="Verbose output.")
def main(
    model: str,
    dataset: str,
    task: tuple[str, ...],
    n_trials: int,
    output_dir: str,
    verbose: bool,
) -> None:
    """Run Terminal-Bench evaluation with gptme.

    Requires gptme with eval extras (installs terminal-bench in the same env):
        pip install 'gptme[eval]'

    Example:
        gptme-eval-tbench --task hello-world
        gptme-eval-tbench --model anthropic/claude-haiku-4-5 --task hello-world --task broken-python
    """
    try:
        subprocess.run(
            ["tb", "--version"], capture_output=True, text=True, check=True, timeout=10
        )
    except FileNotFoundError:
        click.echo(
            "terminal-bench is not installed. Install gptme with eval extras:\n"
            "  pip install 'gptme[eval]'",
            err=True,
        )
        sys.exit(1)
    except subprocess.CalledProcessError:
        click.echo(
            "terminal-bench is installed but 'tb --version' returned a non-zero exit code.\n"
            "It may be broken or misconfigured.",
            err=True,
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        click.echo(
            "terminal-bench version check timed out. It may be unresponsive.",
            err=True,
        )
        sys.exit(1)

    agent_import = "gptme.eval.tbench.agent:GptmeAgent"
    agent_args = json.dumps({"model_name": model})

    cmd = [
        "tb",
        "run",
        "--dataset",
        dataset,
        "--agent-import-path",
        agent_import,
        "--agent-args",
        agent_args,
        "--n-trials",
        str(n_trials),
        "--output-dir",
        output_dir,
    ]

    for t in task:
        cmd += ["--task-id", t]

    if verbose:
        cmd.append("--verbose")

    click.echo(f"Running: {shlex.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
