"""Tests for the memory tool scope parameter (gptme/gptme#4768)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks import ConfirmAction, ConfirmationResult
from gptme.tools import init_tools
from gptme.tools import memory as memory_mod
from gptme.tools.base import ToolUse
from gptme.tools.memory import execute_memory, save_memory


@pytest.fixture
def scoped_workspace(tmp_path, monkeypatch):
    """Workspace with CC and user roots wired to temp dirs."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cc_dir = tmp_path / "cc-memory"
    cc_dir.mkdir()
    user_dir = tmp_path / "config" / "memory"
    user_dir.mkdir(parents=True)

    # Prevent runtime GPTME_MEMORY_DIRS from bypassing the layered-root logic.
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: workspace)
    monkeypatch.setattr("gptme.memory.roots.get_cc_memory_dir", lambda _ws: cc_dir)
    monkeypatch.setattr(
        "gptme.memory.roots.get_config_dir", lambda: tmp_path / "config"
    )
    return {"workspace": workspace, "cc": cc_dir, "user": user_dir}


@pytest.fixture
def worktree_workspace(tmp_path, monkeypatch):
    """Simulate a temporary feature worktree: no project/memory dir, CC-only root."""
    worktree = tmp_path / "worktrees" / "my-feature-abc123"
    worktree.mkdir(parents=True)
    cc_dir = tmp_path / "cc-worktree"
    cc_dir.mkdir()
    user_dir = tmp_path / "config" / "memory"
    user_dir.mkdir(parents=True)

    # Prevent runtime GPTME_MEMORY_DIRS from bypassing the layered-root logic.
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: worktree)
    monkeypatch.setattr("gptme.memory.roots.get_cc_memory_dir", lambda _ws: cc_dir)
    monkeypatch.setattr(
        "gptme.memory.roots.get_config_dir", lambda: tmp_path / "config"
    )
    return {"worktree": worktree, "cc": cc_dir, "user": user_dir}


# --- save_memory() ---


def test_save_memory_default_scope_uses_cc(scoped_workspace):
    """Default scope writes to the cc root (no project/memory dir present)."""
    path = Path(
        save_memory(
            "pref-a", "Description\nBody text.", workspace=scoped_workspace["workspace"]
        )
    )
    assert path.parent == scoped_workspace["cc"]
    assert path.name == "pref-a.md"


def test_save_memory_user_scope_writes_to_user_root(worktree_workspace):
    """scope='user' saves to the global user root, not the worktree CC dir."""
    path = Path(
        save_memory(
            "cross-project-pref",
            "Global preference.\nUse concise answers.",
            workspace=worktree_workspace["worktree"],
            scope="user",
        )
    )
    assert path.parent == worktree_workspace["user"]
    assert path.name == "cross-project-pref.md"
    assert not (worktree_workspace["cc"] / "cross-project-pref.md").exists()


def test_save_memory_invalid_scope_raises():
    """save_memory raises ValueError for an unrecognised scope."""
    with pytest.raises(ValueError, match="invalid scope"):
        save_memory("x", "y", scope="typo-scope")


# --- execute_memory() native kwargs ---


def _auto_confirm():
    return ConfirmationResult(action=ConfirmAction.CONFIRM)


def test_execute_memory_user_scope_saves_to_user_root(worktree_workspace):
    """Native kwargs with scope='user' saves to the global user root."""
    with patch("gptme.hooks.get_confirmation", return_value=_auto_confirm()):
        messages = list(
            execute_memory(
                None,
                None,
                {
                    "name": "user-pref",
                    "content": "Prefer dark mode.\nAll UIs.",
                    "scope": "user",
                },
            )
        )

    assert any("user-pref" in m.content and "saved" in m.content for m in messages)
    saved = worktree_workspace["user"] / "user-pref.md"
    assert saved.exists()
    assert not (worktree_workspace["cc"] / "user-pref.md").exists()


def test_execute_memory_invalid_scope_yields_error(worktree_workspace):
    """Invalid scope yields a system error message, no file written."""
    messages = list(
        execute_memory(
            None,
            None,
            {"name": "bad", "content": "content", "scope": "unknown-scope"},
        )
    )
    assert any(
        "invalid" in m.content.lower() and "scope" in m.content.lower()
        for m in messages
    )
    assert not (worktree_workspace["cc"] / "bad.md").exists()


def test_execute_memory_confirmation_path_matches_save_path(worktree_workspace):
    """The path shown in the confirmation prompt matches the actual saved file."""
    confirmed_path: list[str] = []
    saved_path: list[str] = []

    def capture_confirm(**kwargs):
        confirmed_path.append(kwargs["preview"])
        return ConfirmationResult(action=ConfirmAction.CONFIRM)

    with patch("gptme.hooks.get_confirmation", side_effect=capture_confirm):
        messages = list(
            execute_memory(
                None,
                None,
                {"name": "parity-check", "content": "Description.", "scope": "user"},
            )
        )

    # Extract path from success message
    for m in messages:
        if "parity-check" in m.content and "saved" in m.content:
            # e.g. "Memory 'parity-check' saved to `/path/to/parity-check.md`."
            start = m.content.find("`") + 1
            end = m.content.find("`", start)
            if start > 0 and end > start:
                saved_path.append(m.content[start:end])

    assert saved_path, "no success message found"
    # Confirm message should reference the same file
    assert saved_path[0] in confirmed_path[0], (
        f"confirmation path {confirmed_path[0]!r} does not contain {saved_path[0]!r}"
    )
    # Saved file should actually exist
    assert Path(saved_path[0]).exists()


def test_execute_memory_default_scope_from_worktree_uses_cc(worktree_workspace):
    """Without explicit scope, a worktree session writes to the CC root (default)."""
    with patch("gptme.hooks.get_confirmation", return_value=_auto_confirm()):
        list(
            execute_memory(
                None,
                None,
                {"name": "local-note", "content": "Project note."},
            )
        )

    assert (worktree_workspace["cc"] / "local-note.md").exists()
    assert not (worktree_workspace["user"] / "local-note.md").exists()


@pytest.mark.parametrize(
    ("tool_format", "call"),
    [
        ("markdown", "```memory parsed-pref scope=user\nDescription.\n```"),
        (
            "xml",
            '<tool-use><memory args="parsed-pref scope=user">Description.</memory></tool-use>',
        ),
        (
            "xml",
            '<tool-use><memory name="parsed-pref" scope="user">Description.</memory></tool-use>',
        ),
    ],
)
def test_parsed_scope_saves_to_user_root(worktree_workspace, tool_format, call):
    init_tools(allowlist=["memory"])
    calls = list(ToolUse.iter_from_content(call, tool_format_override=tool_format))
    assert len(calls) == 1
    parsed = calls[0]
    with patch("gptme.hooks.get_confirmation", return_value=_auto_confirm()):
        messages = list(execute_memory(parsed.content, parsed.args, parsed.kwargs))
    assert any("saved" in m.content for m in messages)
    assert (worktree_workspace["user"] / "parsed-pref.md").exists()
    assert not (worktree_workspace["cc"] / "parsed-pref.md").exists()


def test_unavailable_scope_yields_error_before_confirmation(worktree_workspace):
    with patch("gptme.hooks.get_confirmation") as confirm:
        messages = list(
            execute_memory(
                None,
                None,
                {"name": "missing-root", "content": "Description.", "scope": "project"},
            )
        )
    confirm.assert_not_called()
    assert len(messages) == 1
    assert "no memory root" in messages[0].content
    assert "project" in messages[0].content
    assert not (worktree_workspace["cc"] / "missing-root.md").exists()


def test_default_project_scope_and_explicit_cc(scoped_workspace):
    workspace = scoped_workspace["workspace"]
    project = workspace / "memory"
    project.mkdir()
    assert (
        Path(save_memory("project-note", "Description.", workspace=workspace)).parent
        == project
    )
    assert (
        Path(
            save_memory("cc-note", "Description.", workspace=workspace, scope="cc")
        ).parent
        == scoped_workspace["cc"]
    )


@pytest.mark.parametrize("scope", ["user", "cc", "project"])
def test_explicit_roots_reject_unavailable_scopes(
    worktree_workspace, monkeypatch, scope
):
    explicit = worktree_workspace["worktree"] / "explicit"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(explicit))
    with patch("gptme.hooks.get_confirmation") as confirm:
        messages = list(
            execute_memory(
                None,
                None,
                {"name": "no-fallback", "content": "Description.", "scope": scope},
            )
        )
    confirm.assert_not_called()
    assert "no memory root" in messages[0].content
    assert not explicit.exists()


def test_native_name_with_spaces_preserved(worktree_workspace):
    with patch("gptme.hooks.get_confirmation", return_value=_auto_confirm()):
        messages = list(
            execute_memory(
                None,
                None,
                {"name": "My Cool Fact", "content": "Description.", "scope": "user"},
            )
        )
    assert any("saved" in m.content for m in messages)
    assert (worktree_workspace["user"] / "my-cool-fact.md").exists()


@pytest.mark.parametrize(
    "args", [["pref", "scope=invalid"], ["pref", "scope=user", "extra"]]
)
def test_invalid_positional_scope_writes_nothing(worktree_workspace, args):
    with patch("gptme.hooks.get_confirmation") as confirm:
        messages = list(execute_memory("Description.", args))
    confirm.assert_not_called()
    assert "Error" in messages[0].content
    assert list(worktree_workspace["user"].glob("*.md")) == []
    assert list(worktree_workspace["cc"].glob("*.md")) == []


def test_confirmation_edit_cannot_save_preview_metadata(worktree_workspace):
    decision = ConfirmationResult(
        action=ConfirmAction.EDIT, edited_content="Edited body."
    )
    with patch("gptme.hooks.get_confirmation", return_value=decision):
        messages = list(
            execute_memory(
                None,
                None,
                {"name": "edit-note", "content": "Description.", "scope": "user"},
            )
        )
    assert any("Editing is not supported" in m.content for m in messages)
    assert not (worktree_workspace["user"] / "edit-note.md").exists()


def test_native_name_containing_scope_text_is_not_parsed(worktree_workspace):
    with patch("gptme.hooks.get_confirmation", return_value=_auto_confirm()):
        messages = list(
            execute_memory(
                None,
                None,
                {
                    "name": "Preference scope=cc",
                    "content": "Description.",
                    "scope": "user",
                },
            )
        )
    assert any("saved" in m.content for m in messages)
    assert (worktree_workspace["user"] / "preference-scope-cc.md").exists()
