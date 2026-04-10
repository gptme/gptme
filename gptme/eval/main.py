"""
Evals for code generation tools.

Inspired by a document by Anton Osika and Axel Theorell.
"""

import csv
import importlib.util
import json
import keyword
import logging
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import cast, get_args

import click
import multiprocessing_logging
from tabulate import tabulate

from ..config import get_config
from ..message import len_tokens
from ..tools import ToolFormat
from .run import run_evals
from .suites import suites, tests_default, tests_default_ids, tests_map
from .types import CaseResult, EvalResult, EvalSpec, ModelConfig

# Configure logging, including fully-qualified module names
logging.basicConfig(
    level=logging.INFO,
    # helpful in debugging: %(processName)s
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def in_docker() -> bool:
    """Check if currently running inside a Docker container."""
    try:
        with open("/proc/1/cgroup") as f:
            content = f.read()
            return "docker" in content or "containerd" in content
    except FileNotFoundError:
        return False


def docker_reexec(argv: list[str]) -> None:
    """
    Re-execute current command inside Docker container.

    This function:
    1. Checks/builds the gptme-eval Docker image
    2. Mounts config, results, and source code
    3. Re-executes the current command inside Docker
    4. Exits with the same return code
    """
    # Remove argv[0] (provided by Dockerfile entrypoint)
    argv = argv[1:]

    # Remove --use-docker from args
    argv = [arg for arg in argv if arg != "--use-docker"]

    # Get git root
    try:
        git_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            cwd=Path(__file__).parent,
            timeout=10,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("Error: Not in a git repository", file=sys.stderr)
        sys.exit(1)

    # Check/build Docker image
    image = "gptme-eval:latest"
    try:
        subprocess.run(
            ["docker", "image", "inspect", image],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        logger.info(f"Using existing Docker image: {image}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.info(f"Building Docker image: {image}")
        dockerfile = Path(git_root) / "scripts" / "Dockerfile.eval"
        if not dockerfile.exists():
            print(f"Error: Dockerfile not found at {dockerfile}", file=sys.stderr)
            sys.exit(1)
        subprocess.run(
            ["make", "build-docker"],
            cwd=Path(git_root),
            check=True,
            timeout=300,  # 5 min cap for Docker builds
        )

    # Collect environment variables to pass through
    # These are common LLM provider API keys that may be needed
    env_vars_to_pass = [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
    ]

    # Get config to also check config.toml for API keys
    config = get_config()

    # Collect env vars to pass into the container.
    # Use --env-file with a temporary file (mode 0600) so that secret values
    # never appear in the process argument list visible via ``ps aux`` or
    # ``/proc/<pid>/cmdline``.  See CWE-214.
    env_entries: list[str] = []
    for var in env_vars_to_pass:
        # Check both environment variables and config.toml
        value = config.get_env(var)
        if value:
            env_entries.append(f"{var}={value}")

    # Write env vars to a secure temporary file for --env-file.
    # Use tempfile.mkstemp() which atomically creates the file via
    # os.O_CREAT | os.O_EXCL, avoiding the TOCTOU race that
    # NamedTemporaryFile + deferred chmod would introduce.
    # Note: mkstemp requests 0o600 but the kernel applies the process
    # umask, so we follow up with an explicit os.chmod() to guarantee
    # the permissions unconditionally.
    env_file_path: str | None = None
    env_file_args: list[str] = []
    if env_entries:
        fd, env_file_path = tempfile.mkstemp(
            prefix="gptme-docker-env-",
            suffix=".env",
            text=True,
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(env_entries) + "\n")
        except BaseException:
            # os.fdopen took ownership of fd; the context manager already
            # closed it (even on exception), so do NOT call os.close(fd)
            # here — that would raise EBADF and shadow the real error.
            try:
                os.unlink(env_file_path)
            except OSError:
                pass
            env_file_path = None
            raise
        # Unconditionally enforce 0o600 — mkstemp’s mode is subject to umask.
        os.chmod(env_file_path, 0o600)
        env_file_args = ["--env-file", env_file_path]

    # Construct docker run command
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        *env_file_args,
        "-v",
        f"{git_root}/eval_results:/app/eval_results",
        "-v",
        f"{git_root}:/app",
        "-w",
        "/app",
        image,
    ] + argv

    env_var_names = [e.split("=", 1)[0] for e in env_entries]
    # Replace the actual temp path with a placeholder in the log to avoid
    # leaking the env file location to log sinks (see CWE-214).
    logged_cmd = [
        "<env-file>" if i > 0 and docker_cmd[i - 1] == "--env-file" else tok
        for i, tok in enumerate(docker_cmd)
    ]
    logger.info(
        "Re-executing inside Docker: %s (env vars: %s)",
        " ".join(logged_cmd),
        ", ".join(env_var_names) if env_var_names else "none",
    )

    # Run and exit with same code, ensuring env file cleanup
    try:
        result = subprocess.run(docker_cmd, check=False)
        sys.exit(result.returncode)
    finally:
        if env_file_path is not None:
            try:
                os.unlink(env_file_path)
            except OSError:
                pass


project_dir = Path(__file__).parent.parent.parent


def sort_tests(test_names):
    # sorts a list of test names by the order they appear in the default tests
    return sorted(
        test_names,
        key=lambda x: list(tests_map).index(x) if x in tests_map else 0,
    )


def resolve_eval_names(eval_names: list[str]) -> list[EvalSpec]:
    """Resolve eval names/aliases to a deduplicated list of EvalSpecs.

    Handles the 'all' and 'all-practical' aliases, individual test names,
    and suite names. Deduplicates while preserving order.

    Raises ValueError if an eval name is not found.
    """
    evals: list[EvalSpec] = []
    for eval_name in eval_names:
        if eval_name == "all":
            evals.extend(
                test for suite_tests in suites.values() for test in suite_tests
            )
        elif eval_name == "all-practical":
            evals.extend(
                test
                for name, suite_tests in suites.items()
                if name.startswith("practical")
                for test in suite_tests
            )
        elif test := tests_map.get(eval_name):
            evals.append(test)
        elif suite := suites.get(eval_name) or suites.get(eval_name.replace("-", "_")):
            evals.extend(suite)
        else:
            raise ValueError(f"Test or results '{eval_name}' not found")

    # Deduplicate while preserving order
    seen_names: set[str] = set()
    deduped: list[EvalSpec] = []
    for t in evals:
        if t["name"] not in seen_names:
            seen_names.add(t["name"])
            deduped.append(t)
    return deduped


def print_available_tests():
    """Print available eval suites and their tests."""
    default_names = set(tests_default_ids)
    total_tests = 0

    print("Available eval suites:\n")
    for suite_name, suite_tests in suites.items():
        total_tests += len(suite_tests)
        print(f"  {suite_name} ({len(suite_tests)} tests)")
        for test in suite_tests:
            name = test["name"]
            # Truncate prompt for display
            prompt = test.get("prompt", "").replace("\n", " ").strip()
            if len(prompt) > 70:
                prompt = prompt[:67] + "..."
            marker = " *" if name in default_names else ""
            print(f"    {name}{marker}")
            if prompt:
                print(f"      {prompt}")
        print()

    print(f"Total: {total_tests} tests across {len(suites)} suites")
    print(f"Default suite: {', '.join(tests_default_ids)}")
    print("(* = included in default suite)")
    print()
    print("Aliases:")
    print("  all             Run all suites")
    print("  all-practical   Run all practical suites")


def list_available_tests_json() -> dict:
    """Return available eval suites and tests as a JSON-serializable dict."""
    default_names = set(tests_default_ids)
    suites_data = []
    total = 0
    for suite_name, suite_tests in suites.items():
        total += len(suite_tests)
        tests_data = [
            {
                "name": test["name"],
                "prompt": test.get("prompt", ""),
                "default": test["name"] in default_names,
            }
            for test in suite_tests
        ]
        suites_data.append(
            {
                "name": suite_name,
                "tests": tests_data,
            }
        )
    return {
        "suites": suites_data,
        "total_tests": total,
        "default_suite": list(tests_default_ids),
    }


def print_model_results(model_results: dict[ModelConfig, list[EvalResult]]):
    total_tests = 0
    total_tokens = 0

    for config, results in model_results.items():
        print(f"\nResults for model: {config.model} (format: {config.tool_format})")
        model_total_tokens = sum(
            len_tokens(result.gen_stdout, "gpt-4")
            + len_tokens(result.run_stdout, "gpt-4")
            for result in results
        )
        print(f"Completed {len(results)} tests in {model_total_tokens}tok:")
        for result in results:
            cases = result.results
            checkmark = "✅" if cases and all(case.passed for case in cases) else "❌"
            duration_result = (
                result.timings["gen"] + result.timings["run"] + result.timings["eval"]
            )
            gen_tokens = len_tokens(result.gen_stdout, "gpt-4")
            run_tokens = len_tokens(result.run_stdout, "gpt-4")
            result_total_tokens = gen_tokens + run_tokens
            print(
                f"{checkmark} {result.name}: {duration_result:.0f}s/{result_total_tokens}tok "
                f"(gen: {result.timings['gen']:.0f}s/{gen_tokens}tok, "
                f"run: {result.timings['run']:.0f}s/{run_tokens}tok, "
                f"eval: {result.timings['eval']:.0f}s)"
            )
            for case in cases:
                checkmark = "✅" if case.passed else "❌"
                print(f"   {checkmark} {case.name}")

        total_tests += len(results)
        total_tokens += model_total_tokens
    print("\nTotal across all models:")
    print(f"Completed {total_tests} tests in {total_tokens}tok")


def print_model_results_table(model_results: dict[ModelConfig, list[EvalResult]]):
    test_names = sort_tests(
        {result.name for results in model_results.values() for result in results}
    )
    headers = ["Model", "Format"] + list(test_names)
    table_data = []

    for config, results in model_results.items():
        row = [config.model, config.tool_format]
        for test_name in test_names:
            try:
                result = next(r for r in results if r.name == test_name)
                passed = all(case.passed for case in result.results)
                checkmark = (
                    "✅"
                    if result.status == "success" and passed
                    else ("🟡" if result.status == "timeout" else "❌")
                )
                duration = sum(result.timings.values())
                gen_tokens = len_tokens(result.gen_stdout, "gpt-4")
                run_tokens = len_tokens(result.run_stdout, "gpt-4")
                reason = "timeout" if result.status == "timeout" else ""
                if reason:
                    row.append(f"{checkmark} {reason}")
                else:
                    row.append(
                        f"{checkmark} {duration:.0f}s/{gen_tokens + run_tokens}tok"
                    )
            except StopIteration:
                row.append("❌ N/A")
        table_data.append(row)

    print(tabulate(table_data, headers=headers))


def aggregate_and_display_results(result_files: list[str]):
    all_results: dict[ModelConfig, dict[str, dict]] = {}
    for file in result_files:
        for config, model_results in read_results_from_csv(file).items():
            if config not in all_results:
                all_results[config] = {}
            for result in model_results:
                if result.name not in all_results[config]:
                    all_results[config][result.name] = {
                        "total": 0,
                        "passed": 0,
                        "tokens": 0,
                    }
                all_results[config][result.name]["total"] += 1
                all_results[config][result.name]["tokens"] += len_tokens(
                    result.gen_stdout, "gpt-4"
                ) + len_tokens(result.run_stdout, "gpt-4")
                if result.status == "success" and all(
                    case.passed for case in result.results
                ):
                    all_results[config][result.name]["passed"] += 1

    # Prepare table data
    headers = ["Model", "Format"] + sort_tests(
        {test for model_results in all_results.values() for test in model_results}
    )
    table_data = []

    def get_status_emoji(passed, total):
        percentage = (passed / total) * 100
        if percentage >= 80:
            return "✅"
        if 20 <= percentage < 80:
            return "🔶"
        return "❌"

    for config, results in sorted(all_results.items(), key=lambda x: str(x[0])):
        row = [config.model.replace("openrouter/", ""), config.tool_format]
        for test in headers[2:]:
            if test in results:
                passed = results[test]["passed"]
                total = results[test]["total"]
                tokens = results[test]["tokens"]
                status_emoji = get_status_emoji(passed, total)
                incl_tokens = True
                row.append(
                    f"{status_emoji} {passed}/{total}"
                    + (f" {round(tokens / total)}tk" if incl_tokens else "")
                )
            else:
                row.append("❓ N/A")
        table_data.append(row)

    # Print the table
    print(tabulate(table_data, headers=headers))


@click.command()
@click.argument("eval_names_or_result_files", nargs=-1)
@click.option(
    "_model",
    "--model",
    "-m",
    multiple=True,
    help="Model to use, can be passed multiple times. Can include tool format with @, e.g. 'gpt-4@tool'",
)
@click.option(
    "--timeout",
    "-t",
    default=300,
    type=click.IntRange(min=1),
    help="Timeout for code generation (seconds)",
)
@click.option(
    "--parallel",
    "-p",
    default=10,
    type=click.IntRange(min=1),
    help="Number of parallel evals to run",
)
@click.option(
    "--tool-format",
    type=click.Choice(get_args(ToolFormat)),
    help="Tool format to use. Can also be specified per model with @format.",
)
@click.option(
    "--list",
    "-l",
    "list_tests",
    is_flag=True,
    help="List available eval suites and tests, then exit.",
)
@click.option(
    "--use-docker",
    is_flag=True,
    help="Run evals in Docker container for isolation (prevents host environment pollution)",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output results as JSON to stdout (also saves eval_results.json alongside CSV).",
)
@click.option(
    "--eval-module",
    "-E",
    "eval_modules",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Load eval specs from an external Python module file (e.g. generated by speckit-eval gen). "
    "The module must define a 'tests' list of EvalSpec dicts. Can be passed multiple times.",
)
@click.option(
    "--leaderboard",
    is_flag=True,
    help="Generate a model comparison leaderboard from eval_results/ instead of running evals.",
)
@click.option(
    "--leaderboard-format",
    type=click.Choice(["rst", "csv", "markdown", "json", "html"]),
    default="markdown",
    help="Output format for the leaderboard (default: markdown).",
)
@click.option(
    "--min-tests",
    type=int,
    default=4,
    help="Minimum number of tests for a model to appear in the leaderboard (default: 4).",
)
@click.option(
    "--trends",
    is_flag=True,
    help="Show pass-rate trends over time (use with --leaderboard).",
)
@click.option(
    "--trend-days",
    type=int,
    default=90,
    help="Number of days to include in trend analysis (default: 90).",
)
def main(
    eval_names_or_result_files: list[str],
    _model: list[str],
    timeout: int,
    parallel: int,
    list_tests: bool = False,
    tool_format: ToolFormat | None = None,
    use_docker: bool = False,
    json_output: bool = False,
    eval_modules: tuple[Path, ...] = (),
    leaderboard: bool = False,
    leaderboard_format: str = "markdown",
    min_tests: int = 4,
    trends: bool = False,
    trend_days: int = 90,
):
    """
    Run evals for gptme.
    Pass eval or suite names to run, or result files to print.

    Use --leaderboard to generate a model comparison table from existing results.

    Output from evals will be captured, unless a single eval is run, and saved to the results directory.
    """
    if leaderboard:
        results_dir = Path(
            os.environ.get("EVAL_RESULTS_DIR", project_dir / "eval_results")
        )

        if trends:
            from .leaderboard import (
                compute_rate_trends,
                format_trends_html,
                format_trends_markdown,
                load_results,
            )

            try:
                results = load_results(results_dir)
                if not results:
                    raise FileNotFoundError(f"No eval results found in {results_dir}")
                trend_data = compute_rate_trends(
                    results, min_tests=min_tests, window_days=trend_days
                )
                if not trend_data["daily_rates"]:
                    raise ValueError("No trend data available.")
                if leaderboard_format == "html":
                    output = format_trends_html(trend_data)
                else:
                    output = format_trends_markdown(trend_data)
            except (FileNotFoundError, ValueError) as e:
                print(str(e), file=sys.stderr)
                sys.exit(1)
        else:
            from .leaderboard import generate_leaderboard

            try:
                output = generate_leaderboard(
                    results_dir=results_dir,
                    output_format=leaderboard_format,
                    min_tests=min_tests,
                )
            except (FileNotFoundError, ValueError) as e:
                print(str(e), file=sys.stderr)
                sys.exit(1)

        if leaderboard_format == "csv":
            print(output, end="")
        else:
            print(output)
        sys.exit(0)

    if list_tests:
        if eval_modules:
            print("Note: --eval-module tests are not included in this listing.")
        if json_output:
            print(json.dumps(list_available_tests_json(), indent=2))
        else:
            print_available_tests()
        sys.exit(0)

    # Check if we should re-execute inside Docker
    if use_docker and not in_docker():
        logger.info("Re-executing inside Docker container...")
        docker_reexec(sys.argv)
        # docker_reexec will exit, so this line is never reached

    # init
    multiprocessing_logging.install_mp_handler()

    config = get_config()

    # Generate model+format combinations
    default_models = []
    if config.get_env("OPENAI_API_KEY"):
        default_models.extend(
            [
                "openai/gpt-4o@tool",
                "openai/gpt-4o@markdown",
                "openai/gpt-4o@xml",
                "openai/gpt-4o-mini@tool",
            ]
        )
    if config.get_env("ANTHROPIC_API_KEY"):
        default_models.extend(
            [
                "anthropic/claude-sonnet-4-6@tool",
                "anthropic/claude-sonnet-4-6@markdown",
                "anthropic/claude-sonnet-4-6@xml",
                "anthropic/claude-haiku-4-5@tool",
                "anthropic/claude-haiku-4-5@xml",
            ]
        )
    if config.get_env("OPENROUTER_API_KEY"):
        default_models.extend(
            [
                "openrouter/meta-llama/llama-3.1-70b-instruct@xml",
                # "openrouter/meta-llama/llama-3.1-405b-instruct",
            ]
        )
    if config.get_env("GEMINI_API_KEY"):
        default_models.extend(["gemini/gemini-2.5-flash"])

    # Process model specifications into typed ModelConfig objects
    model_configs: list[ModelConfig] = []
    for model_spec in _model or default_models:
        if "@" in model_spec:
            try:
                model_configs.append(ModelConfig.from_spec(model_spec))
                continue
            except ValueError:
                pass  # '@' was part of model name, fall through

        # No format specified (or @ was part of model name): use provided default or test all formats
        formats: list[ToolFormat] = (
            [cast(ToolFormat, tool_format)]
            if tool_format
            else ["markdown", "xml", "tool"]
        )
        model_configs.extend(
            ModelConfig(model=model_spec, tool_format=fmt) for fmt in formats
        )

    results_files = []
    for f in eval_names_or_result_files:
        p = Path(f)
        if p.suffix == ".csv":
            results_files.append(f)
        if (p / "eval_results.csv").exists():
            results_files.append(str(p / "eval_results.csv"))
    eval_names = [f for f in eval_names_or_result_files if f not in results_files]
    if len(results_files) >= 2:
        aggregate_and_display_results(results_files)
        sys.exit(0)
    elif results_files:
        model_results = read_results_from_csv(results_files[0])
        print_model_results(model_results)
        print_model_results_table(model_results)
        sys.exit(0)

    # Load evals from external module files (e.g. generated by speckit-eval gen)
    external_evals: list[EvalSpec] = []
    for module_path in eval_modules:
        module_path = module_path.resolve()
        # Use the file stem as the module name so spawn-based worker processes
        # (default on macOS/Windows) can reimport pickled check functions.
        # Workers start with empty sys.modules and import by name; the name must
        # match a file discoverable on sys.path.
        mod_name = module_path.stem
        # Validate stem is a usable Python module name — spawn-based worker processes
        # (default on macOS/Windows) reimport pickled check functions by module name,
        # which requires the name to be both a valid identifier AND not a reserved
        # keyword (e.g. __import__("class") raises SyntaxError on spawn workers).
        if not mod_name.isidentifier() or keyword.iskeyword(mod_name):
            raise ValueError(
                f"Eval module filename '{module_path.name}' produces module name "
                f"'{mod_name}' which is not a usable Python module name "
                f"(must be a valid identifier and not a reserved keyword). "
                f"Rename the file to use only letters, digits, and underscores "
                f"(no dashes, spaces, or reserved words) so that multiprocessing "
                f"workers can reimport it."
            )
        # Add the module's parent dir to sys.path so worker processes can reimport it.
        # (multiprocessing pickles functions by module+qualname and reimports them)
        # All error paths after this point must remove the entry to avoid leaking.
        parent = str(module_path.parent)
        path_was_absent = parent not in sys.path
        if path_was_absent:
            sys.path.insert(0, parent)
        try:
            # Detect stem collisions (two different files with the same stem) and raise
            # early rather than silently overwriting the first module's check functions.
            if mod_name in sys.modules and getattr(
                sys.modules[mod_name], "__file__", None
            ) != str(module_path):
                raise ValueError(
                    f"Eval module stem collision: '{mod_name}' is already loaded from a "
                    f"different file. Rename your eval module to use a unique filename."
                )
            mod_spec = importlib.util.spec_from_file_location(mod_name, module_path)
            if mod_spec is None or mod_spec.loader is None:
                raise ValueError(f"Could not load eval module: {module_path}")
            mod = importlib.util.module_from_spec(mod_spec)
            sys.modules[mod_name] = mod  # register so pickle can find it
            mod_spec.loader.exec_module(mod)
            if not hasattr(mod, "tests") or not isinstance(mod.tests, list):
                raise ValueError(
                    f"Eval module '{module_path}' must define a 'tests' list of EvalSpec dicts"
                )
        except Exception:
            sys.modules.pop(mod_name, None)  # clean up on exec or validation failure
            if path_was_absent:
                try:
                    sys.path.remove(parent)
                except ValueError:
                    pass
            raise
        loaded: list[EvalSpec] = mod.tests
        logger.info(
            "Loaded %d eval(s) from external module: %s", len(loaded), module_path
        )
        # Detect name collisions between this module's tests and already-loaded external evals
        if external_evals and loaded:
            existing_names = {e["name"] for e in external_evals}
            for spec in loaded:
                if spec["name"] in existing_names:
                    raise ValueError(
                        f"External eval name '{spec['name']}' from '{module_path}' "
                        f"collides with an eval from another --eval-module file. "
                        f"Rename the eval to use a unique name."
                    )
        external_evals.extend(loaded)

    evals_to_run = resolve_eval_names(eval_names)

    # Detect name collisions between external module tests and named/suite evals
    if evals_to_run and external_evals:
        named_set = {e["name"] for e in evals_to_run}
        for ext in external_evals:
            if ext["name"] in named_set:
                raise ValueError(
                    f"External eval name '{ext['name']}' collides with a named/suite eval. "
                    f"Rename the external eval to use a unique name."
                )
    evals_to_run.extend(external_evals)

    if eval_modules and not external_evals:
        if evals_to_run:
            logger.warning(
                "All --eval-module files defined empty 'tests' lists; "
                "running named evals/suites only"
            )
        else:
            logger.warning(
                "All --eval-module files defined empty 'tests' lists; "
                "falling back to default suite"
            )

    if not evals_to_run:
        evals_to_run = tests_default

    if not json_output:
        print("=== Running evals ===")
    model_results = run_evals(
        evals_to_run, model_configs, timeout, parallel, use_docker
    )
    if not json_output:
        print("=== Finished ===")

    if json_output:
        commit_hash = _get_commit_hash()
        json_data = results_to_json(model_results, commit_hash=commit_hash)
        print(json.dumps(json_data, indent=2))
    else:
        json_data = None
        print("\n=== Model Results ===")
        print_model_results(model_results)

        print("\n=== Model Comparison ===")
        print_model_results_table(model_results)

    # Write results to CSV (and JSON if flag set)
    write_results(model_results, write_json=json_output, json_data=json_data)

    sys.exit(0)


def _read_case_results(cases_file: Path) -> Generator[CaseResult, None, None]:
    if cases_file.exists():
        with open(cases_file, newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                yield CaseResult(
                    name=row["Case"],
                    passed=row["Passed"] == "true",
                    duration=float(row["Duration"]),
                )


def _write_case_results(cases_file: Path, results: list[CaseResult]):
    with open(cases_file, "w", newline="") as csvfile:
        fieldnames = ["Case", "Passed", "Duration"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = {
                "Case": result.name,
                "Passed": "true" if result.passed else "false",
                "Duration": round(result.duration, 2),
            }
            writer.writerow(row)


def read_log_file(file_path: Path) -> str:
    if file_path.exists():
        with open(file_path) as f:
            return f.read()
    return ""


def read_results_from_csv(filename: str) -> dict[ModelConfig, list[EvalResult]]:
    model_results: dict[ModelConfig, list[EvalResult]] = defaultdict(list)
    results_dir = Path(filename).parent
    with open(filename, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            model_name = row["Model"]
            tool_format = row.get("Tool Format", "")

            if tool_format:
                # New format: separate columns
                if tool_format not in get_args(ToolFormat):
                    logger.warning(
                        f"Unknown tool format '{tool_format}' in CSV, skipping row"
                    )
                    continue
                config = ModelConfig(
                    model=model_name, tool_format=cast(ToolFormat, tool_format)
                )
                test_dir = results_dir / model_name / tool_format / row["Test"]
                if not test_dir.exists():
                    # Fallback: try legacy flat directory layout
                    test_dir = results_dir / f"{model_name}@{tool_format}" / row["Test"]
            else:
                # Legacy format: model column may contain 'model@format'
                try:
                    config = ModelConfig.from_spec(model_name)
                except ValueError:
                    # No format info at all — use 'markdown' as default
                    config = ModelConfig(model=model_name, tool_format="markdown")
                test_dir = results_dir / model_name / row["Test"]

            result = EvalResult(
                name=row["Test"],
                status="success" if row["Passed"] == "true" else "error",
                results=list(_read_case_results(test_dir / "cases.csv")),
                timings={
                    "gen": float(row["Generation Time"]),
                    "run": float(row["Run Time"]),
                    "eval": float(row["Eval Time"]),
                },
                gen_stdout=read_log_file(test_dir / "gen_stdout.txt"),
                gen_stderr=read_log_file(test_dir / "gen_stderr.txt"),
                run_stdout=read_log_file(test_dir / "run_stdout.txt"),
                run_stderr=read_log_file(test_dir / "run_stderr.txt"),
                log_dir=Path(row.get("Log Dir", str(test_dir))),
                workspace_dir=Path(row.get("Workspace Dir", str(test_dir))),
            )
            model_results[config].append(result)
    return dict(model_results)


def results_to_json(
    model_results: dict[ModelConfig, list[EvalResult]],
    commit_hash: str | None = None,
) -> dict:
    """Convert eval results to a JSON-serializable dict.

    Output schema::

        {
          "timestamp": "2026-03-23T02:30:00Z",
          "commit": "a8b2ef0",
          "models": [
            {
              "model": "anthropic/claude-sonnet-4-20250514",
              "tool_format": "tool",
              "pass_rate": 0.85,
              "total": 20,
              "passed": 17,
              "results": [ { "name": "hello", "status": "success", ... }, ... ]
            }
          ]
        }
    """
    models = []
    for config, results in model_results.items():
        result_dicts = [r.to_dict() for r in results]
        total = len(result_dicts)
        passed = sum(1 for r in result_dicts if r.get("passed"))
        models.append(
            {
                **config.to_dict(),
                "pass_rate": round(passed / total, 4) if total else 0.0,
                "total": total,
                "passed": passed,
                "results": result_dicts,
            }
        )
    return {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "commit": commit_hash,
        "models": models,
    }


def _get_commit_hash() -> str:
    """Get current commit hash and dirty status, like: a8b2ef0-dirty."""
    try:
        git_result = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--exclude", "'*'"],
            check=False,
            text=True,
            capture_output=True,
            cwd=project_dir,
            timeout=10,
        )
        if git_result.returncode == 0 and git_result.stdout.strip():
            return git_result.stdout.strip()
    except subprocess.TimeoutExpired:
        pass
    # not in a git repo or timed out, use package version
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"v{version('gptme')}"
    except PackageNotFoundError:
        return "unknown"


def write_results(
    model_results: dict[ModelConfig, list[EvalResult]],
    write_json: bool = False,
    json_data: dict | None = None,
):
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    commit_hash = _get_commit_hash()
    eval_results_dir = Path(
        os.environ.get("EVAL_RESULTS_DIR", project_dir / "eval_results")
    )
    results_dir = eval_results_dir / timestamp
    results_dir.mkdir(parents=True, exist_ok=True)

    csv_filename = results_dir / "eval_results.csv"

    with open(csv_filename, "w", newline="") as csvfile:
        fieldnames = [
            "Model",
            "Tool Format",
            "Test",
            "Passed",
            "Total Duration",
            "Generation Time",
            "Run Time",
            "Eval Time",
            "Commit Hash",
            "Log Dir",
            "Workspace Dir",
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, lineterminator="\n")

        writer.writeheader()
        for config, results in model_results.items():
            for result in results:
                passed = (
                    all(case.passed for case in result.results)
                    if result.results
                    else False
                )

                # Use model/tool_format/test_name directory layout
                test_dir = results_dir / config.model / config.tool_format / result.name
                test_dir.mkdir(parents=True, exist_ok=True)

                streams = ["gen_stdout", "gen_stderr", "run_stdout", "run_stderr"]
                for stream in streams:
                    stream_file = test_dir / f"{stream}.txt"
                    with open(stream_file, "w", newline="\n") as f:
                        f.write(getattr(result, stream))

                row = {
                    "Model": config.model,
                    "Tool Format": config.tool_format,
                    "Test": result.name,
                    "Passed": "true" if passed else "false",
                    "Total Duration": round(sum(result.timings.values()), 2),
                    "Generation Time": round(result.timings["gen"], 2),
                    "Run Time": round(result.timings["run"], 2),
                    "Eval Time": round(result.timings["eval"], 2),
                    "Commit Hash": commit_hash,
                    "Log Dir": str(result.log_dir),
                    "Workspace Dir": str(result.workspace_dir),
                }
                writer.writerow(row)
                _write_case_results(test_dir / "cases.csv", result.results)

    # When --json is active, status messages go to stderr to keep stdout clean JSON
    _status_file = sys.stderr if write_json else sys.stdout

    if write_json:
        if json_data is None:
            json_data = results_to_json(model_results, commit_hash=commit_hash)
        json_filename = results_dir / "eval_results.json"
        with open(json_filename, "w") as f:
            json.dump(json_data, f, indent=2)
        print(f"\nJSON results saved to {json_filename.resolve()}", file=_status_file)

    print(f"\nResults saved to {csv_filename.resolve()}", file=_status_file)
    print(f"Output files saved in {results_dir.resolve()}", file=_status_file)
