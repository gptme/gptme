"""Regression guard for the pytest-retry x ``tmp_path`` teardown interaction.

pytest's ``tmp_path`` finalizer reads ``node.stash[tmppath_result_key]`` and
deletes it; only tmpdir's ``pytest_runtest_makereport`` hook puts it back.
pytest-retry tears down before each retry (consuming the key) and then re-runs
setup/call through the hooks directly, bypassing ``makereport``. The final
teardown of any retried test using ``tmp_path`` therefore raises
``KeyError: <_pytest.stash.StashKey ...>``.

The damage: a test that *passed on retry* still fails the job, which is exactly
what ``--retries`` exists to prevent. ``tests/retry_compat.py`` re-seeds the key.

Sibling guard for the other retry-machinery bug: see
``tests/test_pytest_timeout_retry_interaction.py``.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

TESTS_DIR = Path(__file__).parent

# Fails on the first attempt, passes on the second, and uses tmp_path -- the
# exact shape that trips the stash bug.
FLAKY_TMP_PATH_TEST = textwrap.dedent(
    """
    _attempts = {"n": 0}


    def test_flaky_using_tmp_path(tmp_path):
        (tmp_path / "artifact.txt").write_text("hello")
        _attempts["n"] += 1
        assert _attempts["n"] > 1, "fails on first attempt only"
    """
)


def _run_isolated_pytest(
    tmp_path: Path, *plugin_args: str
) -> subprocess.CompletedProcess:
    (tmp_path / "test_flake.py").write_text(FLAKY_TMP_PATH_TEST)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_flake.py",
            "-p",
            "no:cacheprovider",
            *plugin_args,
            "-q",
            "--retries",
            "1",
            "--retry-delay",
            "0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": str(TESTS_DIR)},
    )


def test_retried_tmp_path_test_tears_down_cleanly(tmp_path):
    """With retry_compat loaded, a retried tmp_path test reports a clean pass."""
    result = _run_isolated_pytest(tmp_path, "-p", "retry_compat")
    output = result.stdout + result.stderr

    assert "StashKey" not in output, output
    assert " error" not in output, output
    assert result.returncode == 0, f"expected exit 0, got {result.returncode}\n{output}"


def test_without_shim_the_stash_keyerror_still_reproduces(tmp_path):
    """Falsifies the guard above.

    If pytest-retry (or pytest) fixes this upstream, this test starts failing --
    that is the signal to delete ``tests/retry_compat.py`` and this file.
    """
    result = _run_isolated_pytest(tmp_path)
    output = result.stdout + result.stderr

    assert "StashKey" in output, (
        "upstream appears fixed; drop tests/retry_compat.py and this guard\n" + output
    )
