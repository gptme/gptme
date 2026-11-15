"""Exercise the copyable workspace-policy example against save's path formats."""

import textwrap
from pathlib import Path

import pytest

from gptme.hooks.types import ToolExecutePreData
from gptme.tools.base import ToolUse


@pytest.mark.parametrize("markdown", [False, True], ids=["native", "markdown"])
@pytest.mark.parametrize(
    ("destination", "denied"),
    [
        ("notes.txt", False),
        ("nested/notes.txt", False),
        ("{workspace}/notes.txt", False),
        ("{workspace}-other/notes.txt", True),
        ("{workspace}/../outside.txt", True),
        ("../outside.txt", True),
        ("escape/notes.txt", True),
    ],
)
def test_workspace_policy_example(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    destination: str,
    denied: bool,
    markdown: bool,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(workspace)

    # Execute the documented example itself, so copying it stays covered.
    docs = (Path(__file__).resolve().parents[1] / "docs" / "hooks.rst").read_text()
    section = docs.split(".. _pre-execution-deny-seam:", 1)[1]
    example = section.split(".. code-block:: python\n\n", 1)[1].split(
        "\nThis example", 1
    )[0]
    namespace: dict = {}
    exec(textwrap.dedent(example), namespace)

    path = destination.format(workspace=workspace)
    tool_use = (
        ToolUse("save", [path], "hello")
        if markdown
        else ToolUse("save", None, None, kwargs={"path": path, "content": "hello"})
    )
    data = ToolExecutePreData(workspace=workspace, tool_use=tool_use)
    assert list(namespace["deny_writes_outside_workspace"](data)) == []
    assert data.denied is denied
