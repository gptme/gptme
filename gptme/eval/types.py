from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from typing_extensions import NotRequired

Files = dict[str, str | bytes]
Status = Literal["success", "error", "timeout"]


@dataclass
class ResultContext:
    """
    Context for the result of a test.
    """

    files: Files
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class CaseResult:
    """
    Result of a single test case on the execution of a prompt.
    """

    name: str
    passed: bool
    duration: float


@dataclass
class EvalResult:
    """
    Result of executing an eval.
    """

    name: str
    status: Status
    results: list[CaseResult]
    timings: dict[str, float]
    gen_stdout: str
    gen_stderr: str
    run_stdout: str
    run_stderr: str
    log_dir: Path
    workspace_dir: Path


class EvalSpec(TypedDict):
    """
    Specification for an eval/test case.
    """

    name: str
    files: Files
    run: str
    prompt: str
    expect: dict[str, Callable[[ResultContext], bool]]
    tools: NotRequired[list[str]]
