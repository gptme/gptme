import concurrent.futures
import io
import logging
import multiprocessing
import os
import signal
import sys
import time
from collections import defaultdict
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from multiprocessing import Manager, Process
from pathlib import Path
from typing import TypedDict

from tqdm import tqdm

from ..tools import ToolFormat
from .agents import Agent, GPTMe
from .cost import get_eval_costs
from .execenv import DockerExecutionEnv, SimpleExecutionEnv
from .types import (
    CaseResult,
    EvalResult,
    EvalSpec,
    ResultContext,
    Status,
)

logger = logging.getLogger(__name__)


class ProcessSuccess(TypedDict):
    status: str
    files: dict[str, str | bytes]
    stdout: str
    stderr: str
    duration: float
    log_dir: Path
    workspace_dir: Path
    cost: dict | None


class ProcessError(TypedDict):
    status: str
    message: str
    stdout: str
    stderr: str
    duration: float


ProcessResult = ProcessSuccess | ProcessError


def _graceful_killpg(pgrp: int, grace_period: float = 2.0) -> None:
    """Terminate process group gracefully with SIGTERM, then SIGKILL after grace period."""
    try:
        # First, try graceful termination
        os.killpg(pgrp, signal.SIGTERM)
        # Wait for processes to terminate gracefully
        deadline = time.time() + grace_period
        while time.time() < deadline:
            try:
                # Check if process group still exists
                os.killpg(pgrp, 0)  # Signal 0 = check existence
                time.sleep(0.1)
            except ProcessLookupError:
                return  # Process group terminated
        # Grace period expired, force kill
        os.killpg(pgrp, signal.SIGKILL)
    except ProcessLookupError:
        pass  # Process group already terminated


class SyncedDict(TypedDict):
    result: ProcessResult


def run_evals(
    evals: list[EvalSpec],
    model_configs: list[tuple[str, ToolFormat]],  # (model, tool_format)
    timeout: int,
    parallel: int,
    use_docker: bool = False,
) -> dict[str, list[EvalResult]]:
    """
    Run evals for a list of tests.

    Args:
        evals: List of evaluation specifications
        model_configs: List of (model, tool_format) tuples
        timeout: Timeout in seconds for each eval
        parallel: Number of parallel evaluations to run
        use_docker: Run tests in Docker containers for isolation
    """
    # For coverage to work with multiprocessing
    # https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html
    try:
        # noreorder
        from pytest_cov.embed import cleanup_on_sigterm  # fmt: skip  # type: ignore
    except ImportError:
        pass
    else:
        cleanup_on_sigterm()

    n_runs = len(evals) * len(model_configs)
    model_results: dict[str, dict[str, EvalResult]] = defaultdict(dict)
    parallel = min(n_runs, parallel)
    with ProcessPoolExecutor(parallel) as executor:
        futures = []
        future_to_model_test = {}
        for model, tool_format in model_configs:
            model_id = f"{model}@{tool_format}"
            for test in evals:
                tools = test.get(
                    "tools"
                )  # Get tools from test spec, None if not specified
                agent = GPTMe(
                    model=model,
                    tool_format=tool_format,
                    tools=tools,
                    use_docker=use_docker,
                )
                future = executor.submit(
                    execute,
                    test,
                    agent,
                    timeout,
                    parallel > 1,
                    use_docker,
                )
                futures.append(future)
                future_to_model_test[future] = (model_id, test, agent)

        def _handle_future(future: Future):
            model, test, agent = future_to_model_test[future]
            test_name = test["name"]
            try:
                result = future.result(timeout=0.1)
            except Exception as e:
                # TODO: we still want to get stdout/stderr from the process
                gen_time = 0
                if isinstance(e, concurrent.futures.TimeoutError) or isinstance(
                    e, concurrent.futures.CancelledError
                ):
                    status: Status = "timeout"
                    gen_time = timeout
                else:
                    status = "error"
                    logger.exception(
                        f"Test {test_name} for model {model} generated an exception when trying to get result"
                    )
                result = EvalResult(
                    name=test_name,
                    status=status,
                    results=[],
                    timings={"gen": gen_time, "run": 0, "eval": 0},
                    gen_stdout="",
                    gen_stderr="",
                    run_stdout="",
                    run_stderr="",
                    log_dir=agent.log_dir,
                    workspace_dir=agent.workspace_dir,
                )
            model_results[model][test_name] = result

        # worse-case run time, with some buffer to account for overhead
        max_timeout = timeout * len(evals) / parallel + 10
        completed = set()
        try:
            # TODO: can we do better than this? handle timeouts within futures instead?
            for future in tqdm(
                as_completed(futures, timeout=max_timeout),
                total=n_runs,
                unit="eval",
                desc="Progress",
                # ensures it's disabled in non-TTY (such as pytest) and non-parallel
                disable=(None if parallel > 1 else False),
            ):
                _handle_future(future)
                completed.add(future)
        except concurrent.futures.TimeoutError:
            # NOTE: this should rarely happen, as `execute` should handle timeouts
            logger.warning(
                "Timeout reached in top-level (shouldnt happen). Cancelling remaining futures..."
            )

            # Cancel any remaining futures
            for future in futures:
                if future not in completed:
                    future.cancel()
                    _handle_future(future)

    # Ensure all processes are terminated
    for process in multiprocessing.active_children():
        process.terminate()
        process.join()

    model_results_final: dict[str, list[EvalResult]] = defaultdict(list)
    for model in sorted(model_results):
        # sort results by test order
        model_results_final[model] = sorted(
            model_results[model].values(),
            key=lambda result: [test["name"] for test in evals].index(result.name),
        )

    return model_results_final


# TODO: rewrite to run in Docker? Would help with capturing output + process management.
def execute(
    test: EvalSpec, agent: Agent, timeout: int, parallel: bool, use_docker: bool = False
) -> EvalResult:
    """
    Executes the code for a specific model with a timeout.
    """
    logger.info(f'Running "{test["name"]}" for {agent.model}')
    time_gen = 0.0
    time_run = 0.0
    time_eval = 0.0

    with Manager() as manager:
        sync_dict = manager.dict()
        p = Process(
            target=act_process,
            args=(
                agent,
                test["name"],
                test["prompt"],
                test["files"],
                sync_dict,
                parallel,
            ),
        )

        p.start()
        try:
            p.join(timeout)
            status: Status = "success"
            if p.is_alive():
                logger.info("Timeout reached, terminating process")
                status = "timeout"
                time_gen = timeout
                p.terminate()
                p.join(timeout=1)
        finally:
            if p.is_alive():
                p.terminate()
                p.join(timeout=1)
                # Force kill if still alive to prevent race condition on sync_dict
                if p.is_alive():
                    p.kill()
                    p.join()  # Wait for forced termination

        if "result" in sync_dict:
            result = sync_dict["result"]
            time_gen = max(result.get("duration", 0.0), time_gen)
            status = result["status"]
            files = result.get("files", {})
            gen_stdout = result.get("stdout", "")
            gen_stderr = result.get("stderr", "")
            log_dir = result.get("log_dir") or agent.log_dir
            workspace_dir = result.get("workspace_dir") or agent.workspace_dir

            # Extract cost from subprocess result
            cost_dict = result.get("cost")
            cost = None
            if cost_dict:
                from .cost import CostSummary

                cost = CostSummary.from_dict(cost_dict)
        else:
            logger.error("No result in shared dictionary")
            return EvalResult(
                name=test["name"],
                status="error",
                results=[],
                timings={"gen": time_gen, "run": time_run, "eval": time_eval},
                gen_stdout="",
                gen_stderr="",
                run_stdout="",
                run_stderr="",
                log_dir=agent.log_dir,
                workspace_dir=agent.workspace_dir,
            )

        logger.debug("Got result")

        if status != "timeout" and status != "error":
            # check and collect results
            run_start = time.time()
            env = DockerExecutionEnv() if use_docker else SimpleExecutionEnv()
            try:
                env.upload(files)
                logger.debug(f"Running check: {test['run']}")
                stdout_run, stderr_run, exit_code = env.run(test["run"])
                time_run = time.time() - run_start
                files = env.download()
            finally:
                if hasattr(env, "cleanup"):
                    env.cleanup()

            ctx = ResultContext(files, stdout_run, stderr_run, exit_code)
            results: list[CaseResult] = []
            print(f"\n--- Results for '{test['name']}' with {agent.model} ---")
            for name, case in test["expect"].items():
                eval_start = time.time()
                try:
                    passed = case(ctx)
                except Exception as e:
                    print(f"Error while checking {name}: {e}")
                    passed = False
                eval_duration = time.time() - eval_start
                checkmark = "✅" if passed else "❌"
                print(f"{checkmark} {name:20s}")
                results.append(
                    CaseResult(name=name, passed=passed, duration=eval_duration)
                )
            print("--- End of results ---")

            time_eval = sum(r.duration for r in results)
        else:
            results = []
            stdout_run, stderr_run = "", ""

        return EvalResult(
            name=test["name"],
            status=status,
            results=results,
            timings={"gen": time_gen, "run": time_run, "eval": time_eval},
            gen_stdout=gen_stdout,
            gen_stderr=gen_stderr,
            run_stdout=stdout_run,
            run_stderr=stderr_run,
            log_dir=log_dir,
            workspace_dir=workspace_dir,
            cost=cost,
        )


class StreamTee(io.TextIOBase):
    """Capture stdout or stderr to a stream and optionally keep original streams intact."""

    def __init__(self, stream, keep: bool = False):
        self.stream = stream
        self.captured = io.StringIO()
        self.keep_stream = keep

    def write(self, message) -> int:
        self.captured.write(message)
        if self.keep_stream:
            self.stream.write(message)
        return len(message)

    def getvalue(self):
        return self.captured.getvalue()


def act_process(
    agent: Agent,
    test_name: str,
    prompt: str,
    files: dict[str, str | bytes],
    sync_dict: SyncedDict,
    parallel: bool,
):
    # Configure logging for this subprocess
    subprocess_logger = logging.getLogger(f"gptme.eval:{agent.model}@{test_name}")
    subprocess_logger.setLevel(logging.INFO)

    # Runs in a process for each eval
    # each eval has a process group, so we can kill all child processes
    os.setpgrp()
    pgrp = os.getpgrp()

    # Fix #130: Suppress verbose gptme output during optimization
    # Only keep output if not in parallel mode (i.e., interactive testing)
    # During GEPA optimization, suppress full trajectories
    suppress_output = (
        os.environ.get("GPTME_EVAL_SUPPRESS_OUTPUT", "false").lower() == "true"
    )
    if suppress_output:
        # Redirect to null during optimization
        import io

        stdout = StreamTee(io.StringIO(), keep=False)
        stderr = StreamTee(io.StringIO(), keep=False)
        subprocess_logger.info("Output suppression enabled for GEPA optimization")
    else:
        # Normal behavior for interactive testing
        stdout = StreamTee(sys.stdout, keep=not parallel)
        stderr = StreamTee(sys.stderr, keep=not parallel)

    sys.stdout, sys.stderr = stdout, stderr  # type: ignore

    start = time.time()

    def error_handler(e):
        duration = time.time() - start
        if not isinstance(e, KeyboardInterrupt):
            subprocess_logger.error(f"Error: {e}")
        result_error: ProcessError = {
            "status": "error",
            "message": str(e),
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "duration": duration,
        }
        sync_dict["result"] = result_error

        # kill child processes gracefully
        _graceful_killpg(pgrp)

    # handle SIGTERM
    def sigterm_handler(*_):
        error_handler(KeyboardInterrupt("SIGTERM received"))

    signal.signal(signal.SIGTERM, sigterm_handler)

    subprocess_logger.info("Started")
    try:
        files = agent.act(files, prompt)
    except Exception as e:
        error_handler(e)
        return

    duration = time.time() - start

    # Capture cost summary from this subprocess

    cost_summary = get_eval_costs()
    cost_dict = cost_summary.to_dict() if cost_summary else None

    result_success: ProcessSuccess = {
        "status": "success",
        "files": files,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "duration": duration,
        "log_dir": agent.log_dir,
        "workspace_dir": agent.workspace_dir,
        "cost": cost_dict,
    }
    sync_dict["result"] = result_success
    subprocess_logger.info("Success")

    # Reset SIGTERM handler before cleanup to prevent self-termination
    # from overwriting the success result
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # kill child processes gracefully
    _graceful_killpg(pgrp)
