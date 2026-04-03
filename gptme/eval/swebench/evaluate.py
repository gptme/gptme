import logging
import shutil
import subprocess
import time
from pathlib import Path

from gptme.eval.agents import Agent
from gptme.eval.types import CaseResult, EvalResult

from .utils import (
    KEY_INSTANCE_ID,
    KEY_MODEL,
    KEY_PATCH,
    get_file_spans_from_patch,
    load_instances,
    setup_swebench_repo,
    write_predictions_jsonl,
)

logger = logging.getLogger(__name__)


def run_swebench_evaluation(
    agent: Agent,
    dataset_name: str = "princeton-nlp/SWE-bench_Lite",
    split: str = "test",
    instance_ids: list[str] | None = None,
    repo_base_dir: str | None = None,
    output_dir: str | Path | None = None,
) -> tuple[list[EvalResult], Path]:
    """Run SWE-bench evaluation, returning results and path to predictions JSONL.

    The predictions JSONL file can be fed to the official SWE-bench harness::

        python -m swebench.harness.run_evaluation \\
            --predictions_path predictions.jsonl \\
            --run_id my_run \\
            --dataset_name princeton-nlp/SWE-bench_Lite

    Args:
        agent: The agent to evaluate.
        dataset_name: HuggingFace dataset name.
        split: Dataset split to use.
        instance_ids: Specific instance IDs to evaluate, or None for all.
        repo_base_dir: Base directory to clone repos into.
        output_dir: Directory to write predictions.jsonl; defaults to ./runs/swebench.

    Returns:
        (results, predictions_path) tuple.
    """
    logger.info(
        f"Starting SWE-bench evaluation with dataset: {dataset_name}, split: {split}"
    )
    instances = load_instances(dataset_name, split)

    if instance_ids:
        logger.info(f"Filtering instances to: {instance_ids}")
        instances = {id: instances[id] for id in instance_ids if id in instances}

    logger.info(f"Evaluating {len(instances)} instances")

    results = []
    predictions: list[dict] = []

    for instance_id, instance in instances.items():
        logger.info(f"Evaluating instance: {instance_id}")
        result, patch = evaluate_instance(agent, instance, repo_base_dir)
        results.append(result)
        predictions.append(
            {
                KEY_INSTANCE_ID: instance_id,
                KEY_MODEL: agent.model,
                KEY_PATCH: patch,
            }
        )

    logger.info(f"Completed evaluation of {len(results)} instances")

    # Write predictions JSONL for the official SWE-bench harness
    if output_dir is None:
        output_dir = Path("runs/swebench")
    predictions_path = write_predictions_jsonl(
        predictions,
        Path(output_dir) / "predictions.jsonl",
    )
    logger.info(
        f"Predictions written to {predictions_path}. "
        "Run the official harness with:\n"
        f"  python -m swebench.harness.run_evaluation "
        f"--predictions_path {predictions_path} "
        "--run_id gptme_eval "
        f"--dataset_name {dataset_name}"
    )

    return results, predictions_path


def evaluate_instance(
    agent: Agent, instance: dict, repo_base_dir: str | None
) -> tuple[EvalResult, str]:
    """Run agent on a single SWE-bench instance and capture the generated patch.

    Returns:
        (EvalResult, patch) where patch is the unified diff produced by the agent.
        The EvalResult uses a file-coverage heuristic for fast feedback; for
        authoritative pass/fail, feed the patch through the official SWE-bench
        harness (``python -m swebench.harness.run_evaluation``).
    """
    instance_id = instance["instance_id"]
    problem_statement = instance["problem_statement"]

    logger.info(f"Evaluating instance: {instance_id}")
    logger.debug(f"Problem statement: {problem_statement}")

    start_time = time.time()
    patch = ""
    try:
        logger.info(f"Executing agent for instance {instance_id}")
        repo_dir = setup_swebench_repo(instance, repo_base_dir)
        # Copy repo into agent workspace so the agent can actually access and modify files.
        # workspace_dir is the agent's working directory; without this copy the agent
        # would only have access to an empty workspace, not the actual repository.
        shutil.copytree(repo_dir, agent.workspace_dir, dirs_exist_ok=True)
        agent.act(None, problem_statement)
    except Exception as e:
        logger.error(f"Error during agent execution for instance {instance_id}: {e}")
        return (
            EvalResult(
                name=instance_id,
                status="error",
                results=[],
                timings={"gen": time.time() - start_time, "run": 0, "eval": 0},
                gen_stdout="",
                gen_stderr=str(e),
                run_stdout="",
                run_stderr="",
                log_dir=agent.log_dir,
                workspace_dir=agent.workspace_dir,
            ),
            patch,
        )

    gen_time = time.time() - start_time
    logger.info(
        f"Agent execution completed for instance {instance_id} in {gen_time:.2f} seconds"
    )

    # Capture diff from agent workspace (repo copy)
    try:
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=agent.workspace_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        patch = diff_result.stdout
    except subprocess.TimeoutExpired:
        logger.warning(
            f"git diff timed out for instance {instance_id}; treating as empty patch"
        )
        patch = ""

    # Fast heuristic: check whether the patch touches the expected files.
    # NOTE: This is NOT authoritative — use the official SWE-bench harness for
    #       real pass/fail results.
    logger.info(f"Running file-coverage heuristic for instance {instance_id}")
    eval_start = time.time()
    heuristic_pass = _file_coverage_heuristic(instance, patch)
    eval_time = time.time() - eval_start

    logger.info(
        f"Heuristic check for {instance_id}: {'pass' if heuristic_pass else 'fail'} "
        f"(not authoritative — run official harness for real results)"
    )

    return (
        EvalResult(
            name=instance_id,
            status="success",
            results=[
                CaseResult(
                    name="file_coverage_heuristic",
                    passed=heuristic_pass,
                    duration=eval_time,
                )
            ],
            timings={"gen": gen_time, "run": 0, "eval": eval_time},
            gen_stdout="",
            gen_stderr="",
            run_stdout=patch,
            run_stderr="",
            log_dir=agent.log_dir,
            workspace_dir=agent.workspace_dir,
        ),
        patch,
    )


def _file_coverage_heuristic(instance: dict, generated_patch: str) -> bool:
    """Fast heuristic: check if the patch touches the expected files.

    This is NOT equivalent to running the SWE-bench test suite. A patch can
    touch all expected files and still fail, or touch different files and pass.
    Use the official harness for authoritative results.
    """
    expected_spans = instance.get("expected_spans")
    if not expected_spans:
        # Without expected_spans we can't do a coverage check; return True
        # so we don't suppress patches that might be correct.
        logger.debug(
            "No 'expected_spans' in instance — skipping file coverage heuristic"
        )
        return True

    generated_spans = get_file_spans_from_patch(generated_patch)

    for file_path in expected_spans:
        if file_path not in generated_spans:
            logger.info(f"File {file_path} not found in generated patch")
            return False

    logger.info("All expected files found in generated patch")
    return True
