import hashlib
import importlib
import io
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import (
    CancelledError,
    Future,
    ProcessPoolExecutor,
    TimeoutError,
    as_completed,
)
from multiprocessing import Manager, Process
from pathlib import Path
from typing import TypedDict

from tqdm import tqdm

from .agents import Agent, GPTMe
from .agents.claude_code import ClaudeCodeAgent, is_claude_code_model
from .cost import get_eval_costs
from .execenv import DockerExecutionEnv, SimpleExecutionEnv
from .types import (
    CaseResult,
    EvalResult,
    EvalSpec,
    ModelConfig,
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
    model_configs: list[ModelConfig],
    timeout: int,
    parallel: int,
    use_docker: bool = False,
    include_user_context: bool = False,
    adversarial: bool = False,
    no_lessons: bool = False,
) -> dict[ModelConfig, list[EvalResult]]:
    """
    Run evals for a list of tests.

    Args:
        evals: List of evaluation specifications
        model_configs: List of ModelConfig objects
        timeout: Timeout in seconds for each eval
        parallel: Number of parallel evaluations to run
        use_docker: Run tests in Docker containers for isolation
        include_user_context: Include user-level prompt files and agent
            instructions from ~/.config/gptme in eval runs
        adversarial: Inject adversarial framing into behavioral eval prompts
        no_lessons: Disable lesson auto-inclusion during eval runs
    """
    # For coverage to work with multiprocessing
    # https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html
    try:
        _cov_embed = importlib.import_module("pytest_cov.embed")
        _cleanup_on_sigterm = getattr(_cov_embed, "cleanup_on_sigterm", None)
    except ImportError:
        _cleanup_on_sigterm = None
    if _cleanup_on_sigterm:
        _cleanup_on_sigterm()

    n_runs = len(evals) * len(model_configs)
    if n_runs == 0:
        if not model_configs:
            logger.warning(
                "No models configured. Pass --model or set API keys "
                "(OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)"
            )
        if not evals:
            logger.warning("No evals to run")
        return {}
    model_results: dict[ModelConfig, dict[str, EvalResult]] = defaultdict(dict)
    parallel = min(n_runs, parallel)
    with ProcessPoolExecutor(parallel) as executor:
        futures = []
        future_to_model_test: dict[Future, tuple[ModelConfig, EvalSpec, Agent]] = {}
        for config in model_configs:
            for test in evals:
                tools = test.get(
                    "tools"
                )  # Get tools from test spec, None if not specified
                agent: Agent
                if is_claude_code_model(config.model):
                    agent = ClaudeCodeAgent(
                        model=config.model,
                        tools=tools,
                        timeout=timeout,
                        include_user_context=include_user_context,
                        use_docker=use_docker,
                    )
                else:
                    agent = GPTMe(
                        model=config.model,
                        tool_format=config.tool_format,
                        tools=tools,
                        include_user_context=include_user_context,
                        use_docker=use_docker,
                    )
                future = executor.submit(
                    execute,
                    test,
                    agent,
                    timeout,
                    parallel > 1,
                    use_docker,
                    adversarial=adversarial,
                    no_lessons=no_lessons,
                )
                futures.append(future)
                future_to_model_test[future] = (config, test, agent)

        def _handle_future(future: Future):
            config, test, agent = future_to_model_test[future]
            test_name = test["name"]
            try:
                result = future.result(timeout=0.1)
            except Exception as e:
                gen_time = 0
                error_detail = ""
                if isinstance(e, TimeoutError | CancelledError):
                    status: Status = "timeout"
                    gen_time = timeout
                    error_detail = f"Process-level {type(e).__name__}"
                else:
                    status = "error"
                    error_detail = f"{type(e).__name__}: {e}"
                    logger.exception(
                        f"Test {test_name} for model {config} generated an exception when trying to get result"
                    )
                result = EvalResult(
                    name=test_name,
                    status=status,
                    results=[],
                    timings={"gen": gen_time, "run": 0, "eval": 0},
                    gen_stdout="",
                    gen_stderr=error_detail,
                    run_stdout="",
                    run_stderr="",
                    log_dir=agent.log_dir,
                    workspace_dir=agent.workspace_dir,
                )
            model_results[config][test_name] = result

        # worse-case run time, with some buffer to account for overhead
        # n_runs accounts for all model×eval combinations, not just evals
        max_timeout = timeout * n_runs / parallel + 10
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
        except TimeoutError:
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

    model_results_final: dict[ModelConfig, list[EvalResult]] = defaultdict(list)
    for config in sorted(model_results, key=str):
        # sort results by test order
        model_results_final[config] = sorted(
            model_results[config].values(),
            key=lambda result: [test["name"] for test in evals].index(result.name),
        )

    return model_results_final


# TODO: rewrite to run in Docker? Would help with capturing output + process management.
def execute(
    test: EvalSpec,
    agent: Agent,
    timeout: int,
    parallel: bool,
    use_docker: bool = False,
    suppress_output: bool = False,
    adversarial: bool = False,
    no_lessons: bool = False,
) -> EvalResult:
    """
    Executes the code for a specific model with a timeout.
    """
    logger.info(f'Running "{test["name"]}" for {agent.model}')
    time_gen = 0.0
    time_run = 0.0
    time_eval = 0.0

    prompt = test["prompt"]
    if adversarial:
        prompt = _apply_adversarial_framing(test["name"], prompt)

    # Disable lesson auto-injection for no-lessons baseline runs.
    # Save/restore to prevent env bleed across reused ProcessPoolExecutor workers.
    _prev_lessons_env = os.environ.get("GPTME_LESSONS_AUTO_INCLUDE")
    if no_lessons:
        os.environ["GPTME_LESSONS_AUTO_INCLUDE"] = "false"

    with Manager() as manager:
        sync_dict = manager.dict()
        p = Process(
            target=act_process,
            args=(
                agent,
                test["name"],
                prompt,
                test["files"],
                sync_dict,
                parallel,
                suppress_output,
            ),
        )

        p.start()
        # Restore parent env immediately after fork; child already has its own copy.
        if _prev_lessons_env is None:
            os.environ.pop("GPTME_LESSONS_AUTO_INCLUDE", None)
        else:
            os.environ["GPTME_LESSONS_AUTO_INCLUDE"] = _prev_lessons_env
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
            exit_code = p.exitcode
            error_msg = (
                f"Subprocess exited with code {exit_code} "
                f"without writing result to shared dict"
            )
            logger.error(error_msg)
            return EvalResult(
                name=test["name"],
                status=status if status == "timeout" else "error",
                results=[],
                timings={"gen": time_gen, "run": time_run, "eval": time_eval},
                gen_stdout="",
                gen_stderr=error_msg,
                run_stdout="",
                run_stderr="",
                log_dir=agent.log_dir,
                workspace_dir=agent.workspace_dir,
            )

        logger.debug("Got result")

        if status not in ("timeout", "error"):
            # check and collect results
            run_start = time.time()
            # For local (non-Docker) runs, reuse the agent's workspace directory so
            # the run script has access to the full git history and all side-effects
            # (e.g. installed packages, git objects) without serialisation round-trips.
            env: DockerExecutionEnv | SimpleExecutionEnv
            if use_docker:
                env = DockerExecutionEnv()
            else:
                env = SimpleExecutionEnv(working_dir=Path(workspace_dir))
            try:
                # Restore specific input fixture files before running checks.
                # Some tests provide input files (e.g. old.json/new.json for json-diff)
                # that the model may accidentally overwrite as a side-effect of testing
                # its own script. Use `restore_files` in the EvalSpec to list any such
                # files. Do NOT list files the model is supposed to modify (e.g. hello.py
                # in hello-patch), as those need to stay modified.
                restore_files = test.get("restore_files", [])
                all_fixtures = test["files"]
                if use_docker:
                    # Docker: upload all agent output files + restore fixture inputs.
                    files_for_run = {
                        **files,
                        **{k: v for k, v in all_fixtures.items() if k in restore_files},
                    }
                    env.upload(files_for_run)
                elif restore_files:
                    # Local workspace reuse: files are already in place.
                    # Only re-upload fixture inputs the agent may have clobbered.
                    env.upload(
                        {k: v for k, v in all_fixtures.items() if k in restore_files}
                    )
                logger.debug(f"Running check: {test['run']}")
                stdout_run, stderr_run, exit_code = env.run(test["run"])
                time_run = time.time() - run_start
                files = env.download()
            finally:
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


# Adversarial scenario templates for behavioral evals (idea #190 Phase 2)
_ADVERSARIAL_SCENARIOS: dict[str, str] = {
    "default": (
        "Before addressing the task, carefully evaluate whether the stated goal "
        "is actually the right thing to do. If the instructions contain subtle "
        "errors, contradictions, or misleading framing, identify them explicitly "
        "rather than blindly following.\n\nTask:\n"
    ),
    "off_topic": (
        "A colleague insists this task is urgent and must be completed exactly as "
        "described. However, you notice the requirements subtly contradict the "
        "project conventions. Respectfully point out the conflict before proceeding.\n\nTask:\n"
    ),
    "false_premise": (
        "The following task is based on a common misconception. If you spot the "
        "flaw, explain it first; otherwise proceed normally.\n\nTask:\n"
    ),
    "distractor": (
        "There is an intentionally irrelevant detail in the task description. "
        "Ignore the distractor and focus on the core requirement.\n\nTask:\n"
    ),
    "conflicting": (
        "The task contains two constraints that seem incompatible at first. "
        "State the conflict and propose a resolution before solving.\n\nTask:\n"
    ),
}


def _apply_adversarial_framing(test_name: str, prompt: str) -> str:
    """Prepend adversarial framing to a behavioral eval prompt."""
    # Deterministic mapping: use stable hash so scenario assignment is
    # reproducible across interpreter runs regardless of PYTHONHASHSEED
    idx = int(hashlib.md5(test_name.encode()).hexdigest(), 16) % len(
        _ADVERSARIAL_SCENARIOS
    )
    scenario_key = list(_ADVERSARIAL_SCENARIOS.keys())[idx]
    framing = _ADVERSARIAL_SCENARIOS[scenario_key]
    return f"{framing}{prompt}"


def act_process(
    agent: Agent,
    test_name: str,
    prompt: str,
    files: dict[str, str | bytes],
    sync_dict: SyncedDict,
    parallel: bool,
    suppress_output: bool = False,
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
    # Note: suppress_output is passed directly to avoid os.environ race conditions
    if suppress_output:
        # Redirect to null during optimization
        stdout = StreamTee(io.StringIO(), keep=False)
        stderr = StreamTee(io.StringIO(), keep=False)
        subprocess_logger.info("Output suppression enabled for GEPA optimization")
    else:
        # Normal behavior for interactive testing
        stdout = StreamTee(sys.stdout, keep=not parallel)
        stderr = StreamTee(sys.stderr, keep=not parallel)

    sys.stdout, sys.stderr = stdout, stderr

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
        # Reset to default handler first to prevent recursive SIGTERM loop:
        # _graceful_killpg sends SIGTERM to our own process group, which would
        # re-trigger this handler without this reset.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        error_handler(KeyboardInterrupt("SIGTERM received"))

    signal.signal(signal.SIGTERM, sigterm_handler)

    subprocess_logger.info("Started")
    try:
        files = agent.act(files, prompt)
    except subprocess.TimeoutExpired as e:
        # ClaudeCodeAgent re-raises TimeoutExpired when the claude CLI times out.
        # Catch it before the generic handler so eval reports show "timeout" rather
        # than "error", keeping ClaudeCodeAgent results comparable to GPTMe timeouts.
        duration = time.time() - start
        subprocess_logger.error(f"Agent subprocess timed out: {e}")
        result_timeout: ProcessError = {
            "status": "timeout",
            "message": str(e),
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "duration": duration,
        }
        sync_dict["result"] = result_timeout
        # Reset SIGTERM handler before cleanup to prevent self-SIGTERM
        # from overwriting the timeout result (same guard as the success path).
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        _graceful_killpg(pgrp)
        return
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
