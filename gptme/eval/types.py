from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast, get_args

from typing_extensions import NotRequired

if TYPE_CHECKING:
    from .cost import CostSummary

from ..tools import ToolFormat

Files = dict[str, str | bytes]
Status = Literal["success", "error", "timeout"]


@dataclass(frozen=True)
class ModelConfig:
    """Type-safe model + tool format pair, replacing string concatenation with '@'."""

    model: str
    tool_format: ToolFormat

    def __str__(self) -> str:
        return f"{self.model}@{self.tool_format}"

    @classmethod
    def from_spec(
        cls, spec: str, default_format: "ToolFormat | None" = None
    ) -> "ModelConfig":
        """Parse a 'model@format' spec string into a ModelConfig.

        If the spec contains '@' and the suffix is a valid ToolFormat,
        it's treated as a format separator. Otherwise '@' is part of the
        model name (e.g. OpenRouter 'z-ai/glm-5@z-ai').
        """
        if "@" in spec:
            model, fmt = spec.rsplit("@", 1)
            if fmt in get_args(ToolFormat):
                return cls(model=model, tool_format=cast(ToolFormat, fmt))
            # '@' was part of model name, not a format separator
        if default_format:
            return cls(model=spec, tool_format=default_format)
        raise ValueError(f"No tool format in spec '{spec}' and no default provided")


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
    cost: "CostSummary | None" = field(default=None)


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
