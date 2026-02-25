import json
import logging
import shutil
import subprocess
import time

from gptme.eval.agents import Agent
from gptme.eval.types import CaseResult, EvalResult

from .utils import get_file_spans_from_patch, load_instances, setup_swebench_repo

logger = logging.getLogger(__name__)


def run_swebench_evaluation(
    agent: Agent,
    dataset_name: str = "princeton-nlp/SWE-bench_Lite",
    split: str = "test",
    instance_ids: list[str] | None = None,
    repo_base_dir: str | None = None,
) -> list[EvalResult]:
    logger.info(
        f"Starting SWE-bench evaluation with dataset: {dataset_name}, split: {split}"
    )
    instances = load_instances(dataset_name, split)

    if instance_ids:
        logger.info(f"Filtering instances to: {instance_ids}")
        instances = {id: instances[id] for id in instance_ids if id in instances}

    logger.info(f"Evaluating {len(instances)} instances")

    results = []

    for instance_id, instance in instances.items():
        logger.info(f"Evaluating instance: {instance_id}")
        result = evaluate_instance(agent, instance, repo_base_dir)
        results.append(result)

    logger.info(f"Completed evaluation of {len(results)} instances")
    return results


def evaluate_instance(
    agent: Agent, instance: dict, repo_base_dir: str | None
) -> EvalResult:
    instance_id = instance["instance_id"]
    problem_statement = instance["problem_statement"]

    logger.info(f"Evaluating instance: {instance_id}")
    logger.debug(f"Problem statement: {problem_statement}")

    start_time = time.time()
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
        return EvalResult(
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
        )

    gen_time = time.time() - start_time
    logger.info(
        f"Agent execution completed for instance {instance_id} in {gen_time:.2f} seconds"
    )

    # Evaluate the result
    logger.info(f"Evaluating patch for instance {instance_id}")
    eval_start = time.time()
    # Capture the diff from the agent's workspace (which contains the repo copy).
    # Using `git diff` is more reliable than relying on the agent to produce a "diff" file,
    # and avoids the bytes-decoding issue that would arise from str(bytes_obj).
    diff_result = subprocess.run(
        ["git", "diff"],
        cwd=agent.workspace_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    diff = diff_result.stdout
    passed = evaluate_patch(instance, diff)
    eval_time = time.time() - eval_start

    logger.info(f"Evaluation completed for instance {instance_id}. Passed: {passed}")

    return EvalResult(
        name=instance_id,
        status="success",
        results=[
            CaseResult(name="patch_correctness", passed=passed, duration=eval_time)
        ],
        timings={"gen": gen_time, "run": 0, "eval": eval_time},
        gen_stdout="",
        gen_stderr="",
        run_stdout=diff,
        run_stderr="",
        log_dir=agent.log_dir,
        workspace_dir=agent.workspace_dir,
    )


def evaluate_patch(instance: dict, generated_patch: str) -> bool:
    logger.debug(f"Instance keys: {instance.keys()}")
    logger.debug(f"Instance content: {json.dumps(instance, indent=2)}")

    if "expected_spans" not in instance:
        logger.warning(
            "'expected_spans' not found in instance data. Using 'patch' instead."
        )
        expected_patch = instance.get("patch", "")
        logger.debug(f"Expected patch: {expected_patch}")
        logger.debug(f"Generated patch: {generated_patch}")
        return expected_patch.strip() == generated_patch.strip()

    expected_spans = instance["expected_spans"]
    generated_spans = get_file_spans_from_patch(generated_patch)

    logger.debug(f"Expected spans: {expected_spans}")
    logger.debug(f"Generated spans: {generated_spans}")

    for file_path in expected_spans:
        if file_path not in generated_spans:
            logger.info(f"File {file_path} not found in generated patch")
            return False

    logger.info("All expected files found in generated patch")
    return True
