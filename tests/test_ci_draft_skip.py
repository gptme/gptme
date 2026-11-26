"""Heavy Test/Build jobs must skip draft PRs (ci-runner-demand-budget step 4)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

HEAVY_TEST_JOBS = (
    "test-no-api",
    "test-minimal",
    "test-py315",
    "test-windows",
    "test-uv",
    "test-api",
)
HEAVY_BUILD_JOBS = ("build-docker", "build-pyinstaller")

# Exact guard text. Both workflows use the same condition; test.yml ANDs it
# with the job's own trigger condition, build.yml uses it on its own.
DRAFT_GUARD = (
    "(github.event_name != 'pull_request' || github.event.pull_request.draft == false)"
)


def _job_block(workflow: str, job_id: str) -> str:
    """Return the YAML body of a top-level job, up to the next job or EOF."""
    marker = f"  {job_id}:"
    start = workflow.index(marker)
    rest = workflow[start + len(marker) :]
    idx = 0
    while True:
        pos = rest.find("\n", idx)
        if pos == -1:
            return rest
        line = rest[pos + 1 :].split("\n", 1)[0]
        # A sibling job is a 2-space identifier, not a nested 4-space key.
        if (
            line.startswith("  ")
            and not line.startswith("    ")
            and not line.startswith("  #")
            and not line.startswith("  -")
            and line.rstrip().endswith(":")
            and " " not in line.strip()[:-1]
        ):
            return rest[:pos]
        idx = pos + 1


def _job_if(workflow: str, job_id: str) -> str:
    block = _job_block(workflow, job_id)
    # Collect a single-line `if:` or a `|`-folded block until a non-indented-more key.
    lines = block.splitlines()
    collected: list[str] = []
    in_if = False
    if_indent = 0
    for line in lines:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if not in_if:
            if stripped.startswith("if:"):
                in_if = True
                if_indent = indent
                collected.append(stripped[len("if:") :].strip())
                if collected[-1] == "|":
                    collected[-1] = ""
                continue
            continue
        if stripped == "" or stripped.startswith("#"):
            continue
        if indent <= if_indent:
            break
        collected.append(stripped)
    return " ".join(part for part in collected if part)


def _is_single_parenthesized(expr: str) -> bool:
    """True if ``expr`` is one parenthesized group and nothing else.

    ``&&`` binds tighter than ``||`` in GitHub expressions, so the draft guard
    is only a leading conjunct when everything after it is a single group.
    ``(guard) && push || code`` would parse as ``(guard && push) || code`` and
    re-run the heavy job on every draft PR with a code change.
    """
    if not (expr.startswith("(") and expr.endswith(")")):
        return False
    depth = 0
    for i, char in enumerate(expr):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and i != len(expr) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def _assert_leading_draft_guard(expr: str, job_id: str) -> None:
    """The skip must be the leading AND-ed conjunct, not a nested substring.

    A bare substring check also passes for inverted logic such as
    ``(github.event_name != 'pull_request' && github.event.pull_request.draft == false)``,
    which keeps the heavy jobs off every non-draft PR.
    """
    bare_guard = DRAFT_GUARD[1:-1]
    prefix = DRAFT_GUARD + " && "
    rest = expr[len(prefix) :] if expr.startswith(prefix) else None
    assert expr == bare_guard or (
        rest is not None and _is_single_parenthesized(rest)
    ), f"{job_id} draft guard is not AND-ed as the leading conjunct: {expr}"


def test_test_yml_retriggers_when_draft_is_marked_ready() -> None:
    text = (ROOT / ".github/workflows/test.yml").read_text()
    assert "ready_for_review" in text
    assert "types: [opened, synchronize, reopened, ready_for_review]" in text


def test_build_yml_retriggers_when_draft_is_marked_ready() -> None:
    text = (ROOT / ".github/workflows/build.yml").read_text()
    assert "ready_for_review" in text
    assert "types: [opened, synchronize, reopened, ready_for_review]" in text


def test_heavy_test_jobs_skip_draft_pull_requests() -> None:
    text = (ROOT / ".github/workflows/test.yml").read_text()
    for job_id in HEAVY_TEST_JOBS:
        expr = _job_if(text, job_id)
        assert expr, f"{job_id} has no job-level if:"
        _assert_leading_draft_guard(expr, job_id)


def test_heavy_build_jobs_skip_draft_pull_requests() -> None:
    text = (ROOT / ".github/workflows/build.yml").read_text()
    for job_id in HEAVY_BUILD_JOBS:
        expr = _job_if(text, job_id)
        assert expr, f"{job_id} has no job-level if:"
        _assert_leading_draft_guard(expr, job_id)


def test_draft_guard_check_rejects_inverted_logic() -> None:
    """Guard against the guard: a substring-only check would pass this form."""
    inverted = (
        "(github.event_name != 'pull_request' && "
        "github.event.pull_request.draft == false) && "
        "(github.event_name == 'push')"
    )
    with pytest.raises(AssertionError):
        _assert_leading_draft_guard(inverted, "example")


def test_draft_guard_check_rejects_unparenthesized_remainder() -> None:
    """A dropped outer paren makes the guard a non-leading conjunct."""
    dropped = (
        DRAFT_GUARD
        + " && github.event_name == 'push' || needs.changes.outputs.code == 'true'"
    )
    with pytest.raises(AssertionError):
        _assert_leading_draft_guard(dropped, "example")


def test_draft_skip_does_not_drop_merge_queue_or_master_push() -> None:
    """The skip is AND-ed with existing triggers; push and merge_group stay live."""
    text = (ROOT / ".github/workflows/test.yml").read_text()
    for job_id in ("test-no-api", "test-minimal", "test-uv"):
        expr = _job_if(text, job_id)
        assert "github.event_name == 'push'" in expr, job_id
        assert "github.event_name == 'merge_group'" in expr, job_id
    # test-py315 is push-or-deps; test-windows is push-or-code. Neither
    # listed merge_group before this PR — do not add a Windows queue job.
    for job_id in ("test-py315", "test-windows"):
        expr = _job_if(text, job_id)
        assert "github.event_name == 'push'" in expr, job_id
        assert "merge_group" not in expr, job_id
