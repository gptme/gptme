"""Heavy Test/Build jobs must skip draft PRs (ci-runner-demand-budget step 4)."""

from pathlib import Path

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
        assert "github.event.pull_request.draft == false" in expr, (
            f"{job_id} if: does not skip drafts: {expr}"
        )


def test_heavy_build_jobs_skip_draft_pull_requests() -> None:
    text = (ROOT / ".github/workflows/build.yml").read_text()
    for job_id in HEAVY_BUILD_JOBS:
        expr = _job_if(text, job_id)
        assert expr, f"{job_id} has no job-level if:"
        assert "github.event.pull_request.draft == false" in expr, (
            f"{job_id} if: does not skip drafts: {expr}"
        )


def test_draft_skip_does_not_drop_merge_queue_or_master_push() -> None:
    """The skip is AND-ed with existing triggers; push and merge_group stay live."""
    text = (ROOT / ".github/workflows/test.yml").read_text()
    for job_id in ("test-no-api", "test-minimal", "test-uv", "test-windows"):
        expr = _job_if(text, job_id)
        assert "github.event_name == 'push'" in expr, job_id
        assert "github.event_name == 'merge_group'" in expr, job_id
    # test-py315 is push-or-deps, not merge_group; do not broaden it.
    py315 = _job_if(text, "test-py315")
    assert "github.event_name == 'push'" in py315
    assert "merge_group" not in py315
