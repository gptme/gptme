"""Exercise reader context with real locks, isolated from pytest's agent context."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# The holder announces readiness only after writing a real conversation and
# acquiring its lock. Stdin keeps it alive without timing-dependent sleeps.
_HOLDER = """
import sys
from pathlib import Path
from gptme.logmanager import LogManager
from gptme.message import Message
with LogManager([Message("assistant", "live child output")], logdir=Path(sys.argv[1])) as manager:
    manager.write()
    print("ready", flush=True)
    sys.stdin.readline()
"""


def _run_isolated(tmp_path: Path, body: str) -> None:
    # LogManager deliberately bypasses locking under pytest; neither subprocess
    # may inherit this flag or the failure regression would never acquire a lock.
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    code = (
        """
import subprocess
import sys
from pathlib import Path
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.subagent import Subagent
root = Path(sys.argv[1])
child_dir = root / "child"
holder = subprocess.Popen(
    [sys.executable, "-c", sys.argv[2], str(child_dir)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
try:
    assert holder.stdout.readline().strip() == "ready"
"""
        + "\n".join("    " + line for line in body.splitlines())
        + """
finally:
    holder.communicate("stop\\n", timeout=10)
    assert holder.returncode == 0
"""
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), _HOLDER],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("with_parent", [False, True])
def test_failed_lock_preserves_current_log(tmp_path: Path, with_parent: bool) -> None:
    """A foreign process's live lock must not publish a half-built manager."""
    _run_isolated(
        tmp_path,
        f"""
parent = LogManager([Message("user", "parent")], logdir=root / "parent") if {with_parent!r} else None
assert LogManager.get_current_log() is parent
try:
    LogManager.load(child_dir)
except RuntimeError as exc:
    assert "Another gptme instance" in str(exc), str(exc)
else:
    raise AssertionError("foreign process lock was not enforced")
assert LogManager.get_current_log() is parent
if parent is not None:
    assert parent.log[-1].content == "parent"
""",
    )


@pytest.mark.parametrize("reader", ["get_log", "read_log"])
def test_live_child_read_preserves_parent(tmp_path: Path, reader: str) -> None:
    """Inspection succeeds while the child owns its lock, without adopting it."""
    _run_isolated(
        tmp_path,
        f"""
from gptme.tools.subagent import _subagents, subagent_read_log
parent = LogManager([Message("user", "parent")], logdir=root / "parent")
child = Subagent(agent_id="live-child", prompt="test", thread=None, logdir=child_dir, model=None)
if {reader!r} == "get_log":
    loaded = child.get_log()
    assert loaded.log[-1].content == "live child output"
    assert loaded._lock_fd is None
else:
    _subagents.append(child)
    assert "live child output" in subagent_read_log("live-child")
assert holder.poll() is None
assert LogManager.get_current_log() is parent
assert parent.log[-1].content == "parent"
""",
    )
