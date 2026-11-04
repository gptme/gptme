"""Tests for native (structured kwargs) invocation of the memory tool."""

from unittest.mock import patch

import pytest

from gptme.hooks import ConfirmAction, ConfirmationResult
from gptme.tools import memory as memory_mod
from gptme.tools.memory import execute_memory


@pytest.fixture
def memory_workspace(tmp_path, monkeypatch):
    """Redirect the memory workspace and CC memory dir into tmp_path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mem_dir = tmp_path / "cc-memory"
    mem_dir.mkdir()
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: workspace)
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    monkeypatch.setattr("gptme.memory.roots.get_cc_memory_dir", lambda _ws: mem_dir)
    return mem_dir


def test_native_call_reaches_confirmation_with_content(memory_workspace):
    decision = ConfirmationResult(action=ConfirmAction.SKIP)
    with (
        patch(
            "gptme.tools.memory.execute_with_confirmation",
            wraps=memory_mod.execute_with_confirmation,
        ) as spy,
        patch("gptme.hooks.get_confirmation", return_value=decision) as confirm,
    ):
        messages = list(
            execute_memory(
                None, None, {"name": "native-probe", "content": "Test content"}
            )
        )

    assert spy.call_count == 1
    assert confirm.call_count == 1
    assert confirm.call_args.kwargs["preview"] == "Test content"
    assert not any("requires a name" in m.content for m in messages)


def test_native_skip_writes_no_file(memory_workspace):
    decision = ConfirmationResult(action=ConfirmAction.SKIP)
    with patch("gptme.hooks.get_confirmation", return_value=decision):
        list(
            execute_memory(
                None, None, {"name": "native-probe", "content": "Test content"}
            )
        )

    assert not (memory_workspace / "native-probe.md").exists()
    assert list(memory_workspace.glob("*.md")) == []


def test_legacy_call_still_reaches_confirmation(memory_workspace):
    decision = ConfirmationResult(action=ConfirmAction.SKIP)
    with patch("gptme.hooks.get_confirmation", return_value=decision) as confirm:
        list(execute_memory("Legacy body", ["legacy-name"], None))

    assert confirm.call_count == 1
    assert confirm.call_args.kwargs["preview"] == "Legacy body"


def test_native_user_scope_from_temporary_worktree(tmp_path, monkeypatch):
    """Cross-project feedback must not be stranded under a worktree slug."""
    from gptme.dirs import get_cc_memory_dir
    from gptme.memory import MemoryStore

    workspace = tmp_path / "temporary-feature-worktree"
    workspace.mkdir()
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: workspace)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    target = tmp_path / "config" / "gptme" / "memory" / "review-in-parallel.md"
    decision = ConfirmationResult(action=ConfirmAction.CONFIRM)
    with patch("gptme.hooks.get_confirmation", return_value=decision) as confirm:
        messages = list(
            execute_memory(
                None,
                None,
                {
                    "name": "review-in-parallel",
                    "content": "Check reviews concurrently with CI.",
                    "scope": "user",
                },
            )
        )

    assert target.is_file(), [m.content for m in messages]
    assert not (get_cc_memory_dir(workspace) / target.name).exists()
    assert confirm.call_args.kwargs["preview"] == "Check reviews concurrently with CI."
    assert str(target) in messages[-1].content
    assert "scope: user" in messages[-1].content
    assert str(target) in messages[0].content
    assert "user" in messages[0].content
    assert "review-in-parallel" in (target.parent / "MEMORY.md").read_text()
    other = tmp_path / "other-project"
    other.mkdir()
    entry = MemoryStore.from_workspace(other).get("review-in-parallel")
    assert entry is not None
    assert entry.scope == "user"


@pytest.mark.parametrize("scope", [None, "project", "cc", "agent", "user", "explicit"])
def test_selected_root_and_index(tmp_path, monkeypatch, scope):
    from gptme.memory import MemoryStore

    workspace = tmp_path / "workspace"
    (workspace / "memory").mkdir(parents=True)
    agent = tmp_path / "agent"
    (agent / "memory").mkdir(parents=True)
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: workspace)
    monkeypatch.setattr(
        "gptme.memory.roots.get_cc_memory_dir", lambda _ws: tmp_path / "cc"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "custom-config"))
    monkeypatch.setenv("GPTME_AGENT_WORKSPACE", str(agent))
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    if scope == "explicit":
        monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "configured-root"))
    root = MemoryStore.from_workspace(workspace).root(scope)
    kwargs = {"name": "scoped-fact", "content": "A fact.\nDetail."}
    if scope is not None:
        kwargs["scope"] = scope
    with patch(
        "gptme.hooks.get_confirmation",
        return_value=ConfirmationResult(action=ConfirmAction.CONFIRM),
    ) as confirm:
        messages = list(execute_memory(None, None, kwargs))
    path = root.path / "scoped-fact.md"
    assert path.is_file()
    assert confirm.call_args.kwargs["preview"] == "A fact.\nDetail."
    assert str(path) in messages[-1].content
    assert f"scope: {root.scope}" in messages[-1].content
    assert f"scope: {root.scope}" in messages[0].content
    assert str(path) in messages[0].content
    assert "scoped-fact" in (root.path / "MEMORY.md").read_text()
    # Legacy roots upsert pointers rather than replacing curated index prose.
    entry = MemoryStore([root]).get("scoped-fact", scope=root.scope)
    assert entry is not None
    assert entry.index_line() in (root.path / "MEMORY.md").read_text()


@pytest.mark.parametrize("scope", ["invalid", "project", "agent", "user"])
def test_unknown_or_unavailable_scope_no_write(tmp_path, monkeypatch, scope):
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "only-explicit"))
    with patch("gptme.hooks.get_confirmation") as confirm:
        messages = list(
            execute_memory(
                None,
                None,
                {
                    "name": "no-fallback",
                    "content": "Must not fall back.",
                    "scope": scope,
                },
            )
        )
    assert "Error selecting memory scope" in messages[0].content
    assert scope in messages[0].content
    assert "available: explicit" in messages[0].content
    confirm.assert_not_called()
    assert not (tmp_path / "only-explicit").exists()


def test_markdown_scope_round_trip(tmp_path, monkeypatch):
    from gptme.tools.base import ToolUse

    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path / "configured"))
    calls = list(
        ToolUse.iter_from_content(
            "```memory markdown-fact --scope explicit\nSummary.\nBody.\n```",
            tool_format_override="markdown",
        )
    )
    assert len(calls) == 1
    with patch(
        "gptme.hooks.get_confirmation",
        return_value=ConfirmationResult(action=ConfirmAction.CONFIRM),
    ):
        messages = list(
            execute_memory(calls[0].content, calls[0].args, calls[0].kwargs)
        )
    assert "scope: explicit" in messages[0].content
    assert (tmp_path / "configured" / "markdown-fact.md").is_file()


@pytest.mark.parametrize(
    "extra", [["--scope"], ["--other", "user"], ["--scope", "user", "extra"]]
)
def test_markdown_bad_arguments_rejected(memory_workspace, extra):
    with patch("gptme.hooks.get_confirmation") as confirm:
        messages = list(execute_memory("A fact.", ["fact", *extra], None))
    assert "Error: expected" in messages[0].content
    confirm.assert_not_called()
    assert not (memory_workspace / "fact.md").exists()


def test_confirmation_pins_root_even_when_environment_changes(tmp_path, monkeypatch):
    original = tmp_path / "original"
    other = tmp_path / "other"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(original))

    def confirm(**kwargs):
        assert kwargs["preview"] == "Summary."
        monkeypatch.setenv("GPTME_MEMORY_DIRS", str(other))
        return ConfirmationResult(action=ConfirmAction.CONFIRM)

    with patch("gptme.hooks.get_confirmation", side_effect=confirm):
        messages = list(execute_memory("Summary.", ["fact"], None))
    assert (original / "fact.md").is_file()
    assert not other.exists()
    assert str(original / "fact.md") in messages[0].content


def test_save_memory_explicit_scope(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    path = Path(
        memory_mod.save_memory(
            "preference", "User feedback.", workspace=tmp_path, scope="user"
        )
    )
    assert path == tmp_path / "config" / "gptme" / "memory" / "preference.md"
    with pytest.raises(KeyError, match="no memory root"):
        memory_mod.save_memory("bad", "No.", workspace=tmp_path, scope="invalid")


def test_tool_respects_selected_index_policy(tmp_path, monkeypatch):
    import json

    from gptme.memory import MemoryRoot, MemoryStore

    root = tmp_path / "managed"
    root.mkdir()
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    store = MemoryStore([MemoryRoot("explicit", root)])
    store.save("selected", "Old summary.")
    policy = root / ".memory-index.json"
    policy.write_text(
        json.dumps({"version": 1, "budget": 4096, "selected": ["selected.md"]})
    )
    store.write_index()
    with patch(
        "gptme.hooks.get_confirmation",
        return_value=ConfirmationResult(action=ConfirmAction.CONFIRM),
    ):
        list(execute_memory("New summary.", ["selected", "--scope", "explicit"]))
        list(execute_memory("Recall only.", ["new-fact", "--scope", "explicit"]))
    assert store.check_index()
    index = (root / "MEMORY.md").read_text()
    assert "New summary." in index
    assert "new-fact" not in index
    entry = store.get("new-fact")
    assert entry is not None
    assert entry.description == "Recall only."


def test_examples_preserve_scope_in_native_and_markdown_formats():
    import json

    native = memory_mod.examples("tool")
    payload = native.split("@memory: ", 1)[1].split("\n> System:", 1)[0]
    assert json.loads(payload) == {
        "name": "prefer-short-answers",
        "content": "User prefers short, direct answers without preamble.",
        "scope": "user",
    }
    assert "memory prefer-short-answers --scope user" in memory_mod.examples("markdown")


def test_edited_content_keeps_scope_and_path(tmp_path, monkeypatch):
    from gptme.memory import parse_entry

    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    with patch(
        "gptme.hooks.get_confirmation",
        side_effect=[
            ConfirmationResult(
                action=ConfirmAction.EDIT,
                edited_content="Edited summary.\nEdited detail.",
            ),
            ConfirmationResult(action=ConfirmAction.CONFIRM),
        ],
    ) as confirm:
        messages = list(execute_memory("Original.", ["edited", "--scope", "explicit"]))
    assert confirm.call_count == 2
    assert confirm.call_args_list[0].kwargs["preview"] == "Original."
    assert (
        confirm.call_args_list[1].kwargs["preview"] == "Edited summary.\nEdited detail."
    )
    assert str(tmp_path / "edited.md") in messages[0].content
    assert "scope: explicit" in messages[0].content
    entry = parse_entry(tmp_path / "edited.md")
    assert entry.description == "Edited summary."
    assert entry.body == "Edited detail."


def test_confirmation_edit_of_actual_preview_does_not_save_header(
    tmp_path, monkeypatch
):
    from gptme.memory import parse_entry

    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    previews = []

    def confirm(**kwargs):
        previews.append(kwargs["preview"])
        if len(previews) == 1:
            return ConfirmationResult.edit(
                kwargs["preview"].replace("Original.", "Edited.")
            )
        return ConfirmationResult.confirm()

    with patch("gptme.hooks.get_confirmation", side_effect=confirm):
        list(execute_memory("Original.\nDetail.", ["fact", "--scope", "explicit"]))
    entry = parse_entry(tmp_path / "fact.md")
    assert entry.description == "Edited."
    assert entry.body == "Detail."
    assert all(p.count("Save memory") <= 1 for p in previews)


@pytest.mark.parametrize("scope", ["user", "cc", "project", "invalid"])
def test_markdown_workspace_scopes(tmp_path, monkeypatch, scope):
    from gptme.memory import MemoryStore
    from gptme.tools.base import ToolUse

    workspace = tmp_path / "workspace"
    (workspace / "memory").mkdir(parents=True)
    monkeypatch.setattr(memory_mod, "get_workspace", lambda: workspace)
    monkeypatch.setattr(
        "gptme.memory.roots.get_cc_memory_dir", lambda _ws: tmp_path / "cc"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("GPTME_MEMORY_DIRS", raising=False)
    call = list(
        ToolUse.iter_from_content(
            f"```memory fact --scope {scope}\nSummary.\nDetail.\n```",
            tool_format_override="markdown",
        )
    )[0]
    with patch(
        "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
    ) as confirm:
        messages = list(execute_memory(call.content, call.args, call.kwargs))
    if scope == "invalid":
        assert "Error selecting memory scope" in messages[0].content
        confirm.assert_not_called()
    else:
        root = MemoryStore.from_workspace(workspace).root(scope)
        assert (root.path / "fact.md").is_file()
        assert str(root.path / "fact.md") in messages[0].content
        assert str(root.path / "fact.md") in messages[-1].content
