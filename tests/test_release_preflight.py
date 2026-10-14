"""Exercise the release workflow's CI gate with offline GitHub API fixtures."""

import os
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


@pytest.mark.parametrize(
    ("scenario", "expected", "diagnostic"),
    [
        ("scheduled_failure", "true", ""),
        ("second_page_failure", "false", "failures or cancelled"),
        ("pending", "false", "still running"),
        ("suites_error", "false", "API rate limit exceeded"),
        ("checks_error", "false", "API rate limit exceeded"),
        ("suites_partial_error", "false", "API rate limit exceeded"),
        ("checks_partial_error", "false", "API rate limit exceeded"),
        ("empty", "false", "unavailable"),
    ],
)
def test_release_ci_gate(
    tmp_path: Path, scenario: str, expected: str, diagnostic: str
) -> None:
    # Execute the actual workflow snippet, not a second implementation of it.
    workflow = WORKFLOW.read_text()
    snippet = workflow.split("          # Verify CI is green on HEAD\n", 1)[1]
    snippet = snippet.split("\n  # ── Prepare", 1)[0]
    snippet = "\n".join(line[10:] for line in snippet.splitlines())
    snippet = snippet.replace("${{ github.repository }}", "gptme/gptme")
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "scenario = os.environ['SCENARIO']\n"
        "suites = 'actions/runs?' in sys.argv[2]\n"
        "if scenario == ('suites_error' if suites else 'checks_error'):\n"
        "    print('gh: API rate limit exceeded', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "if suites:\n"
        "    assert 'event=push' in sys.argv[2]\n"
        "    ids = [1]\n"
        "    if scenario == 'second_page_failure' and '--paginate' in sys.argv:\n"
        "        ids += [2]\n"
        "    query = sys.argv[sys.argv.index('--jq') + 1]\n"
        "    if query.startswith('['):\n"
        "        print(json.dumps(ids))\n"
        "    else:\n"
        "        for suite in ids: print(suite)\n"
        "else:\n"
        "    checks = [] if scenario == 'empty' else [\n"
        "        {'app': {'slug': 'github-actions'}, 'check_suite': {'id': 1},\n"
        "         'name': 'test', 'conclusion': None if scenario == 'pending' else 'success'},\n"
        "        {'app': {'slug': 'github-actions'}, 'check_suite': {'id': 2},\n"
        "         'name': 'test', 'conclusion': 'failure'},\n"
        "        {'app': {'slug': 'github-actions'}, 'check_suite': {'id': 1},\n"
        "         'name': 'API tests external', 'conclusion': 'failure'},\n"
        "    ]\n"
        "    print(json.dumps({'check_runs': checks}))\n"
        "if scenario == ('suites_partial_error' if suites else 'checks_partial_error'):\n"
        "    print('gh: API rate limit exceeded', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    fake_gh.chmod(0o755)
    output = tmp_path / "output"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "SCENARIO": scenario,
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", snippet],
        cwd=WORKFLOW.parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert output.read_text().strip() == f"should_release={expected}"
    assert diagnostic in result.stdout + result.stderr
