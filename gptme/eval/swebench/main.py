from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from gptme.eval.types import EvalResult

from ..agents import GPTMe
from ..main import write_results
from . import run_swebench_evaluation

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--model",
    "-m",
    multiple=True,
    help="Model to use, can be passed multiple times.",
)
@click.option(
    "--dataset",
    default="princeton-nlp/SWE-bench_Lite",
    help="SWE-bench dataset to use",
)
@click.option(
    "--split",
    default="test",
    help="SWE-bench dataset split to use",
)
@click.option(
    "--instance",
    "-i",
    multiple=True,
    help="Specific SWE-bench instance IDs to evaluate",
)
@click.option(
    "--repo-base-dir",
    help="Base directory for repositories",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Increase output verbosity",
)
def main(
    model: list[str],
    dataset: str,
    split: str,
    instance: list[str],
    repo_base_dir: str,
    verbose: bool,
):
    """Run SWE-bench evaluation for gptme."""
    if verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose output enabled")

    if not model:
        model = [
            "openai/gpt-4o",
            "anthropic/claude-3-5-sonnet-20240620",
        ]

    print("=== Running SWE-bench evaluation ===")
    swebench_results = {}
    for m in model:
        agent = GPTMe(model=m)
        results: list[EvalResult] = run_swebench_evaluation(
            agent,
            dataset_name=dataset,
            split=split,
            instance_ids=instance or None,
            repo_base_dir=repo_base_dir,
        )
        swebench_results[m] = results

    print("\n=== SWE-bench Results ===")
    # TODO: Implement custom result printing for SWE-bench

    # Write SWE-bench results to CSV
    write_results(swebench_results)


if __name__ == "__main__":
    main()
