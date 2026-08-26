"""Regression guard for the pytest-timeout x pytest-retry interaction.

pytest-timeout's default (protocol-wide) SIGALRM stays armed across
pytest-retry's retry loop, which sleeps ``--retry-delay`` between attempts
inside ``pytest_runtest_makereport``. If the alarm fires during that sleep it
raises ``Failed`` from inside a report hook, crashing the xdist worker and
turning the whole session into an INTERNALERROR (exit 3) -- even when every
other test passed.

``timeout_func_only = true`` in ``[tool.pytest.ini_options]`` scopes the alarm
to the test function, so retry delays fall outside the armed window.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

FAILING_TEST = "def test_always_fails():\n    assert False, 'boom'\n"

# Small enough to keep the test fast; retry sleep (2s) still exceeds timeout (1s),
# which is the condition that triggers the crash.
RETRY_ARGS = ["--timeout", "1", "--retries", "1", "--retry-delay", "2"]


def _run_isolated_pytest(tmp_path: Path, ini_body: str) -> subprocess.CompletedProcess:
    (tmp_path / "test_flake.py").write_text(FAILING_TEST)
    (tmp_path / "pytest.ini").write_text(f"[pytest]\n{ini_body}")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_flake.py",
            "-p",
            "no:cacheprovider",
            "-q",
            *RETRY_ARGS,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_retry_delay_does_not_internalerror_with_func_only_timeout(tmp_path):
    """A retried failure must report as a plain failure, not crash the session."""
    result = _run_isolated_pytest(tmp_path, "timeout_func_only = true\n")

    assert "INTERNALERROR" not in result.stdout + result.stderr, result.stdout
    # exit 1 == tests failed (expected); exit 3 == internal error (the bug)
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\n{result.stdout}"
    )


@pytest.mark.slow
@pytest.mark.skipif(
    os.name == "nt",
    reason="SIGALRM-based xdist crash is POSIX-only; thread timer on Windows behaves differently",
)
def test_protocol_scoped_timeout_still_reproduces_the_crash(tmp_path):
    """Falsifies the guard above: without func_only the crash still happens.

    If pytest-timeout or pytest-retry ever fixes this upstream, this test fails
    and ``timeout_func_only`` can be reconsidered.
    """
    result = _run_isolated_pytest(tmp_path, "")

    assert "INTERNALERROR" in result.stdout + result.stderr, result.stdout
    assert result.returncode == 3, (
        f"expected exit 3, got {result.returncode}\n{result.stdout}"
    )


def test_hanging_test_body_is_still_caught(tmp_path):
    """func_only must not disable timeouts for the thing they exist to catch."""
    (tmp_path / "test_hang.py").write_text(
        textwrap.dedent(
            """
            import time

            def test_hangs():
                time.sleep(30)
            """
        )
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\ntimeout_func_only = true\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_hang.py",
            "-p",
            "no:cacheprovider",
            "-q",
            "--timeout",
            "1",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert "Timeout (>1.0s) from pytest-timeout" in result.stdout, result.stdout
    assert result.returncode == 1


def test_repo_config_scopes_timeouts_to_the_function(pytestconfig):
    """The actual protection: gptme's own pytest config must keep this on."""
    assert pytestconfig.getini("timeout_func_only") is True
