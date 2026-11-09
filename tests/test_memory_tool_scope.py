"""Tests for the memory tool scope parameter (gptme/gptme#4768)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks import ConfirmAction, ConfirmationResult
from gptme.tools import memory as memory_mod
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
        confirmed_path.append(kwargs.get("confirm_msg", ""))
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
    assert saved_path[0] in confirmed_path[0] or confirmed_path[0] == "", (
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
