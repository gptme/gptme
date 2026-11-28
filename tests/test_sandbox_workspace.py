"""Sandbox roots belong to conversations, not inherited thread contexts."""

from contextvars import Context, copy_context
from pathlib import Path

import pytest

from gptme.util.workspace import (
    get_sandbox_root,
    pin_sandbox_root,
    set_workspace_cwd,
)


def test_child_workspace_replaces_inherited_sandbox_root(tmp_path: Path) -> None:
    parent_root = tmp_path / "parent"
    child_root = tmp_path / "child"
    parent = Context()
    parent.run(set_workspace_cwd, str(parent_root))
    child = parent.run(copy_context)

    child.run(set_workspace_cwd, str(child_root))

    assert child.run(get_sandbox_root) == child_root.resolve()
    assert parent.run(get_sandbox_root) == parent_root.resolve()
    # Shell restarts/promotions at a different cwd still cannot widen the root.
    child.run(pin_sandbox_root, tmp_path)
    assert child.run(get_sandbox_root) == child_root.resolve()
    child.run(set_workspace_cwd, str(child_root))
    assert child.run(get_sandbox_root) == child_root.resolve()


@pytest.mark.parametrize("test_case", ["first", "second"])
def test_sandbox_root_is_isolated_between_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_case: str
) -> None:
    """Both cases run in the pytest worker's shared context, like shell tests."""
    from gptme.util.workspace import _sandbox_root

    assert _sandbox_root.get() is None
    monkeypatch.chdir(tmp_path)
    pin_sandbox_root(tmp_path / test_case)
    assert get_sandbox_root() == (tmp_path / test_case).resolve()
