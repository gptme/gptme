from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from gptme.eval.types import EvalResult

from ..agents import GPTMe
from ..main import write_results
from ..types import ModelConfig
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
    "--output-dir",
    default="runs/swebench",
    show_default=True,
    help="Directory for predictions.jsonl and results",
)
@click.option(
    "--run-harness",
    is_flag=True,
    default=False,
    help=(
        "After generating patches, run the official SWE-bench harness to evaluate "
        "them. Requires Docker and swebench[evaluation] extras. "
        "Without this flag, only the fast file-coverage heuristic is used."
    ),
)
@click.option(
    "--run-id",
    default="gptme_eval",
    show_default=True,
    help="Run ID passed to the official SWE-bench harness (used for log directory naming).",
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
    output_dir: str,
    run_harness: bool,
    run_id: str,
    verbose: bool,
):
    """Run SWE-bench evaluation for gptme.

    Generates a predictions.jsonl file that can be fed to the official SWE-bench
    harness for authoritative pass/fail results and leaderboard submission.

    Quick start (single instance, no Docker)::

        gptme-eval-swebench -m anthropic/claude-3-5-sonnet-20241022 \\
            -i django__django-11099

    Full evaluation with official harness (requires Docker)::

        gptme-eval-swebench -m anthropic/claude-3-5-sonnet-20241022 --run-harness

    After generation, run the official harness manually::

        python -m swebench.harness.run_evaluation \\
            --predictions_path runs/swebench/predictions.jsonl \\
            --run_id gptme_eval \\
            --dataset_name princeton-nlp/SWE-bench_Lite
    """
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
    all_predictions_paths: list[Path] = []

    for m in model:
        agent = GPTMe(model=m)
        results: list[EvalResult]
        results, predictions_path = run_swebench_evaluation(
            agent,
            dataset_name=dataset,
            split=split,
            instance_ids=instance or None,
            repo_base_dir=repo_base_dir,
            output_dir=Path(output_dir) / m.replace("/", "__"),
        )
        swebench_results[ModelConfig.from_spec(m, default_format="markdown")] = results
        all_predictions_paths.append(predictions_path)

    print("\n=== SWE-bench Results (file-coverage heuristic) ===")
    write_results(swebench_results)

    if all_predictions_paths:
        print("\n=== Predictions Files ===")
        for p in all_predictions_paths:
            print(f"  {p}")
        print(
            "\nTo run official evaluation (requires Docker):\n"
            "  python -m swebench.harness.run_evaluation \\\n"
            f"      --predictions_path {all_predictions_paths[0]} \\\n"
            f"      --run_id {run_id} \\\n"
            f"      --dataset_name {dataset}"
        )

    if run_harness:
        if not all_predictions_paths:
            print("No predictions generated — skipping harness run.", file=sys.stderr)
            return
        if len(all_predictions_paths) > 1:
            print(
                f"Warning: --run-harness only runs the harness for the first model "
                f"({all_predictions_paths[0]}). "
                "Run again with a single -m to evaluate other models.",
                file=sys.stderr,
            )
        # Run the official harness for the first model's predictions
        # (multi-model harness runs can be done by calling this multiple times)
        predictions_path = all_predictions_paths[0]
        print(f"\n=== Running official SWE-bench harness for {predictions_path} ===")
        cmd = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--predictions_path",
            str(predictions_path),
            "--run_id",
            run_id,
            "--dataset_name",
            dataset,
            "--split",
            split,
        ]
        logger.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(
                "Official harness exited with non-zero code. "
                "Check Docker is running and swebench[evaluation] is installed.",
                file=sys.stderr,
            )
            sys.exit(result.returncode)


if __name__ == "__main__":
    main()
