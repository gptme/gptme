"""Exercise the workflow's real Bash gate without GitHub writes or real waits."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github/workflows/dependabot-automerge.yml"
)
HEAD = "a" * 40
OLD = "b" * 40


def workflow_job() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["automerge"]


def run_gate(
    tmp_path: Path, reviews: list[str], *, same_deps: bool = False
) -> tuple[str, str]:
    if os.name == "nt" or not shutil.which("bash") or not shutil.which("jq"):
        pytest.skip("The Ubuntu workflow harness requires POSIX Bash and jq")
    job = workflow_job()
    script = next(step["run"] for step in job["steps"] if step.get("id") == "greptile")
    # Functions intercept only the external boundary. jq still runs for real,
    # including pagination selection; date/sleep advance a virtual clock.
    (tmp_path / "reviews.json").write_text(json.dumps(reviews))
    (tmp_path / "clock").write_text("5400")
    (tmp_path / "poll").write_text("0")
    (tmp_path / "output").touch()
    (tmp_path / "gh").write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys\n"
        "root = pathlib.Path(os.environ['CASE_DIR'])\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'pr':\n"
        "    with (root / 'comments').open('a') as f: f.write(args[-1] + '\\n')\n"
        "elif '/commits/' in args[1]:\n"
        "    version = '1' if args[1].endswith(os.environ['SHA']) or os.environ['SAME_DEPS'] == '1' else '2'\n"
        "    print('- dependency-name: demo\\n  dependency-version: ' + version)\n"
        "else:\n"
        "    n = int((root / 'poll').read_text())\n"
        "    reviews = json.loads((root / 'reviews.json').read_text())\n"
        "    body = reviews[min(n, len(reviews) - 1)] if reviews else ''\n"
        "    bodies = [{'user': {'login': 'TimeToBuildBob'}, 'body': body}] if body else []\n"
        "    if '--slurp' in args:\n"
        "        (root / 'poll').write_text(str(n + 1))\n"
        "        print(json.dumps([bodies]))\n"
        "    else:\n"
        "        query = args[args.index('--jq') + 1]\n"
        "        subprocess.run(['jq', '-r', query], input=json.dumps(bodies), text=True, check=True)\n"
    )
    (tmp_path / "gh").chmod(0o755)
    shim = """
    date() {
      if [ "$1" = +%s ]; then cat "$CASE_DIR/clock"; else echo 0; fi
    }
    sleep() {
      now=$(cat "$CASE_DIR/clock")
      echo "$((now + $1))" > "$CASE_DIR/clock"
    }
    """
    env = dict(
        os.environ,
        PATH=f"{tmp_path}:{os.environ['PATH']}",
        CASE_DIR=str(tmp_path),
        SHA=HEAD,
        PR="123",
        REPO="gptme/gptme",
        CREATED="1970-01-01T00:00:00Z",
        GITHUB_OUTPUT=str(tmp_path / "output"),
        SAME_DEPS=str(int(same_deps)),
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", shim + script],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    comments = (
        (tmp_path / "comments").read_text() if (tmp_path / "comments").exists() else ""
    )
    return (tmp_path / "output").read_text(), comments


def review(sha: str, score: int = 5) -> str:
    return "<!-- bob-ai-review " + json.dumps({"sha": sha, "score": score}) + " -->"


def test_stale_bob_review_waits_for_current_review(tmp_path: Path) -> None:
    output, comments = run_gate(tmp_path, [review(OLD), review(HEAD)])
    assert "ok=true" in output
    assert "Not auto-merging" not in comments
    assert int((tmp_path / "poll").read_text()) == 2


def test_stale_low_score_does_not_block_new_review(tmp_path: Path) -> None:
    output, _ = run_gate(tmp_path, [review(OLD, 3), review(HEAD)])
    assert "ok=true" in output


def test_stale_low_score_with_same_dependencies_waits_for_new_review(
    tmp_path: Path,
) -> None:
    output, _ = run_gate(tmp_path, [review(OLD, 3), review(HEAD)], same_deps=True)
    assert "ok=true" in output
    assert int((tmp_path / "poll").read_text()) == 2


def test_stale_low_score_with_same_dependencies_times_out(tmp_path: Path) -> None:
    output, _ = run_gate(tmp_path, [review(OLD, 3)], same_deps=True)
    assert "ok=true" not in output
    assert "No review (Greptile or Bob) within the time window" in output
    assert int((tmp_path / "clock").read_text()) == 135 * 60


@pytest.mark.parametrize("score", [3, 5])
def test_published_short_sha_review_matches_head(tmp_path: Path, score: int) -> None:
    body = (
        "Confidence 1/5\n<!-- bob-ai-review "
        + json.dumps(
            {"sha": HEAD[:12], "score": score, "history": [{"sha": OLD, "score": 1}]}
        )
        + " -->"
    )
    output, _ = run_gate(tmp_path, [body])
    if score == 5:
        assert "ok=true" in output
    else:
        assert "ok=true" not in output
        assert "Bob confidence is 3/5" in output


def test_rebase_with_same_dependencies_accepts_old_review(tmp_path: Path) -> None:
    output, _ = run_gate(tmp_path, [review(OLD)], same_deps=True)
    assert "ok=true" in output


def test_current_low_score_holds(tmp_path: Path) -> None:
    output, comments = run_gate(tmp_path, [review(HEAD, 3)])
    assert "ok=true" not in output
    assert "Bob confidence is 3/5" in output


@pytest.mark.parametrize("reviews", [[], [review(OLD)]])
def test_missing_or_stale_review_times_out(tmp_path: Path, reviews: list[str]) -> None:
    output, comments = run_gate(tmp_path, reviews)
    assert "ok=true" not in output
    assert "No review (Greptile or Bob) within the time window" in output
    assert int((tmp_path / "clock").read_text()) == 135 * 60


def test_job_budget_and_app_token_lifetime() -> None:
    job = workflow_job()
    assert job["timeout-minutes"] >= 145
    steps = job["steps"]
    gate_index = next(i for i, step in enumerate(steps) if step.get("id") == "greptile")
    token_index = next(
        i for i, step in enumerate(steps) if step.get("id") == "app-token"
    )
    wait_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Wait for Greptile"
    )
    assert wait_index < token_index < gate_index
    assert steps[wait_index]["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "sleep" not in steps[gate_index]["run"].split("bob_deadline=")[0]
    assert (
        steps[gate_index]["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
    )
