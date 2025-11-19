"""
Evals for code generation tools.

Inspired by a document by Anton Osika and Axel Theorell.
"""

import csv
import logging
import os
import subprocess
import sys
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
from .suites import suites, tests_default, tests_map
from .types import CaseResult, EvalResult, EvalSpec

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
        ).strip()
    except subprocess.CalledProcessError:
        print("Error: Not in a git repository", file=sys.stderr)
        sys.exit(1)

    # Check/build Docker image
    image = "gptme-eval:latest"
    try:
        subprocess.run(
            ["docker", "image", "inspect", image], check=True, capture_output=True
        )
        logger.info(f"Using existing Docker image: {image}")
    except subprocess.CalledProcessError:
        logger.info(f"Building Docker image: {image}")
        dockerfile = Path(git_root) / "scripts" / "Dockerfile.eval"
        if not dockerfile.exists():
            print(f"Error: Dockerfile not found at {dockerfile}", file=sys.stderr)
            sys.exit(1)
        subprocess.run(
            ["make", "build-docker"],
            cwd=Path(git_root),
            check=True,
        )

    # Construct docker run command
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        # use separate config dir for gptme-eval (only stores API keys)
        f"{Path.home()}/.config/gptme-eval:/home/appuser/.config/gptme",
        "-v",
        f"{git_root}/eval_results:/app/eval_results",
        "-v",
        f"{git_root}:/app",
        "-w",
        "/app",
        image,
    ] + argv

    logger.info(f"Re-executing inside Docker: {' '.join(docker_cmd)}")

    # Run and exit with same code
    result = subprocess.run(docker_cmd)
    sys.exit(result.returncode)


project_dir = Path(__file__).parent.parent.parent


def sort_tests(test_names):
    # sorts a list of test names by the order they appear in the default tests
    return sorted(
        test_names,
        key=lambda x: (list(tests_map).index(x) if x in tests_map else 0),
    )


def print_model_results(model_results: dict[str, list[EvalResult]]):
    total_tests = 0
    total_tokens = 0

    for model, results in model_results.items():
        print(f"\nResults for model: {model}")
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


def print_model_results_table(model_results: dict[str, list[EvalResult]]):
    test_names = sort_tests(
        {result.name for results in model_results.values() for result in results}
    )
    headers = ["Model"] + list(test_names)
    table_data = []

    for model, results in model_results.items():
        row = [model]
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
                        f"{checkmark} {duration:.0f}s/{gen_tokens+run_tokens}tok"
                    )
            except StopIteration:
                row.append("❌ N/A")
        table_data.append(row)

    print(tabulate(table_data, headers=headers))


def aggregate_and_display_results(result_files: list[str]):
    all_results: dict[str, dict[str, dict]] = {}
    for file in result_files:
        for model, model_results in read_results_from_csv(file).items():
            if model not in all_results:
                all_results[model] = {}
            for result in model_results:
                if result.name not in all_results[model]:
                    all_results[model][result.name] = {
                        "total": 0,
                        "passed": 0,
                        "tokens": 0,
                    }
                all_results[model][result.name]["total"] += 1
                all_results[model][result.name]["tokens"] += len_tokens(
                    result.gen_stdout, "gpt-4"
                ) + len_tokens(result.run_stdout, "gpt-4")
                if result.status == "success" and all(
                    case.passed for case in result.results
                ):
                    all_results[model][result.name]["passed"] += 1

    # Prepare table data
    headers = ["Model"] + sort_tests(
        {
            test
            for model_results in all_results.values()
            for test in model_results.keys()
        }
    )
    table_data = []

    def get_status_emoji(passed, total):
        percentage = (passed / total) * 100
        if 80 <= percentage:
            return "✅"
        elif 20 <= percentage < 80:
            return "🔶"
        else:
            return "❌"

    for model, results in sorted(all_results.items()):
        row = [model.replace("openrouter/", "")]
        for test in headers[1:]:
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
@click.option("--timeout", "-t", default=30, help="Timeout for code generation")
@click.option("--parallel", "-p", default=10, help="Number of parallel evals to run")
@click.option(
    "--tool-format",
    type=click.Choice(get_args(ToolFormat)),
    help="Tool format to use. Can also be specified per model with @format.",
)
@click.option(
    "--use-docker",
    is_flag=True,
    help="Run evals in Docker container for isolation (prevents host environment pollution)",
)
def main(
    eval_names_or_result_files: list[str],
    _model: list[str],
    timeout: int,
    parallel: int,
    tool_format: ToolFormat | None = None,
    use_docker: bool = False,
):
    """
    Run evals for gptme.
    Pass eval or suite names to run, or result files to print.

    Output from evals will be captured, unless a single eval is run, and saved to the results directory.
    """
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
                "anthropic/claude-3-5-sonnet-20241022@tool",
                "anthropic/claude-3-5-sonnet-20241022@markdown",
                "anthropic/claude-3-5-sonnet-20241022@xml",
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
        default_models.extend(["gemini/gemini-1.5-flash-latest"])

    def parse_format(fmt: str) -> ToolFormat:
        if fmt not in get_args(ToolFormat):
            raise ValueError(f"Invalid tool format: {fmt}")
        return cast(ToolFormat, fmt)

    # Process model specifications
    model_configs: list[tuple[str, ToolFormat]] = []
    for model_spec in _model or default_models:
        if "@" in model_spec:
            model, fmt = model_spec.split("@", 1)
            model_configs.append((model, parse_format(fmt)))
        else:
            # If no format specified for model, use either provided default or test all formats
            formats: list[ToolFormat] = (
                [cast(ToolFormat, tool_format)]
                if tool_format
                else ["markdown", "xml", "tool"]
            )
            for fmt in formats:
                model_configs.append((model_spec, fmt))

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

    evals_to_run: list[EvalSpec] = []
    for eval_name in eval_names:
        if test := tests_map.get(eval_name):
            evals_to_run.append(test)
        elif suite := suites.get(eval_name) or suites.get(eval_name.replace("-", "_")):
            evals_to_run.extend(suite)
        else:
            raise ValueError(f"Test or results '{eval_name}' not found")

    if not evals_to_run:
        evals_to_run = tests_default

    print("=== Running evals ===")
    model_results = run_evals(
        evals_to_run, model_configs, timeout, parallel, use_docker
    )
    print("=== Finished ===")

    print("\n=== Model Results ===")
    print_model_results(model_results)

    print("\n=== Model Comparison ===")
    print_model_results_table(model_results)

    # Write results to CSV
    write_results(model_results)

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
        fieldnames = ["Model", "Test", "Case", "Passed", "Duration"]
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


def read_results_from_csv(filename: str) -> dict[str, list[EvalResult]]:
    model_results = defaultdict(list)
    results_dir = Path(filename).parent
    with open(filename, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            model = row["Model"]
            test_dir = results_dir / model / row["Test"]

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
                log_dir=Path(row.get("Log Dir", test_dir)),
                workspace_dir=Path(row.get("Workspace Dir", test_dir)),
            )
            model_results[model].append(result)
    return dict(model_results)


def write_results(model_results: dict[str, list[EvalResult]]):
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    # get current commit hash and dirty status, like: a8b2ef0-dirty
    # TODO: don't assume we are in the gptme repo, use other version identifiers if available
    commit_hash = subprocess.run(
        ["git", "describe", "--always", "--dirty", "--exclude", "'*'"],
        text=True,
        capture_output=True,
    ).stdout.strip()
    eval_results_dir = Path(
        os.environ.get("EVAL_RESULTS_DIR", project_dir / "eval_results")
    )
    results_dir = eval_results_dir / timestamp
    results_dir.mkdir(parents=True, exist_ok=True)

    csv_filename = results_dir / "eval_results.csv"

    with open(csv_filename, "w", newline="") as csvfile:
        fieldnames = [
            "Model",
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
        for model, results in model_results.items():
            for result in results:
                passed = (
                    all(case.passed for case in result.results)
                    if result.results
                    else False
                )

                test_dir = results_dir / model / result.name
                test_dir.mkdir(parents=True, exist_ok=True)

                streams = ["gen_stdout", "gen_stderr", "run_stdout", "run_stderr"]
                for stream in streams:
                    stream_file = test_dir / f"{stream}.txt"
                    with open(stream_file, "w", newline="\n") as f:
                        f.write(getattr(result, stream))

                row = {
                    "Model": model,
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

    print(f"\nResults saved to {csv_filename.resolve()}")
    print(f"Output files saved in {results_dir.resolve()}")
