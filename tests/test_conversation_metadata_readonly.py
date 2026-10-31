"""Metadata discovery must not turn legacy histories into active sessions."""

import json
import shutil
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import gptme.logmanager.conversations as conversations
from gptme.cli.cmd_chats import chats
from gptme.config import ChatConfig


@pytest.fixture(autouse=True)
def init_() -> None:
    """These disk-only tests need neither model nor tool initialization."""


@pytest.fixture
def logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect discovery and all environment-based paths to synthetic data."""
    root = tmp_path / "logs"
    root.mkdir()
    monkeypatch.setenv("GPTME_LOGS_HOME", str(root))
    for variable, directory in (
        ("HOME", "home"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
    ):
        monkeypatch.setenv(variable, str(tmp_path / directory))
    # Real CLI tool initialization may ensure its config directory exists.
    # Preseed it so the snapshot detects history mutations, not that setup.
    (tmp_path / "config" / "gptme").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture(params=[True, False], ids=["full", "fast"])
def detail(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Generator[bool, None, None]:
    """Require fast-mode cases to execute the real tail scanner exactly once."""
    full_detail = bool(request.param)
    original_scan = conversations._fast_scan_tail
    scan_calls = 0

    def scan_tail(path: Path, file_size: int) -> tuple[int, str | None, bytes | None]:
        nonlocal scan_calls
        scan_calls += 1
        assert file_size > conversations._TAIL_BYTES
        return original_scan(path, file_size)

    monkeypatch.setattr(conversations, "_fast_scan_tail", scan_tail)
    yield full_detail
    assert scan_calls == (0 if full_detail else 1)


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    """Record entries and bytes, including directories and dangling symlinks."""
    result: dict[str, tuple[str, bytes | str | None]] = {}
    for path in root.rglob("*"):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[key] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            result[key] = ("directory", None)
        else:
            result[key] = ("file", path.read_bytes())
    return result


def _make_history(logs_dir: Path, name: str = "legacy-history") -> Path:
    history = logs_dir / name
    history.mkdir()
    (history / "conversation.jsonl").write_text(
        json.dumps(
            {
                "role": "system",
                # Cross the real scan threshold without changing message counts
                # or timestamps. Fast cases must read this multi-chunk JSONL.
                "content": "Synthetic abandoned session "
                + "x" * conversations._TAIL_BYTES,
                "timestamp": "2025-01-01T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return history


def _metadata(
    logs_dir: Path, lookup: str, detail: bool
) -> conversations.ConversationMeta:
    if lookup == "list":
        items = list(conversations.get_conversations(detail=detail))
        assert len(items) == 1
        return items[0]
    item = conversations.get_conversation_meta_direct(
        "legacy-history", detail=detail, logs_dir=logs_dir
    )
    assert item is not None
    return item


def _assert_history_metadata(
    item: conversations.ConversationMeta, history: Path, workspace: Path
) -> None:
    assert item.id == history.name
    assert item.path == str(history / "conversation.jsonl")
    assert item.messages == 1
    assert item.branches == 1
    assert item.created == 1735689600.0
    assert item.workspace == str(workspace.resolve())


@pytest.mark.parametrize("lookup", ["list", "direct"])
@pytest.mark.parametrize(
    "config_kind", ["missing", "log", "persisted", "malformed", "unreadable"]
)
def test_metadata_reads_are_noncreating(
    logs_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    lookup: str,
    detail: bool,
    config_kind: str,
) -> None:
    history = _make_history(logs_dir)
    config_path = history / "config.toml"
    workspace = history / "workspace"
    expected_name = history.name
    agent = logs_dir.parent / "agent"
    if config_kind == "log":
        config_path.write_text(
            '[chat]\nname = "Log workspace"\nworkspace = "@log"\n', encoding="utf-8"
        )
        expected_name = "Log workspace"
    elif config_kind == "persisted":
        workspace = logs_dir.parent / "project"
        workspace.mkdir()
        (workspace / "keep.txt").write_bytes(b"existing workspace contents\n")
        agent.mkdir()
        (agent / "gptme.toml").write_text(
            '[agent]\nname = "Metadata Bot"\navatar = "assets/avatar.png"\n'
            '[agent.urls]\nrepo = "https://example.org/metadata"\n',
            encoding="utf-8",
        )
        config_path.write_text(
            '[chat]\nname = "Saved display name"\n'
            f"workspace = {json.dumps(str(workspace))}\n"
            f"agent = {json.dumps(str(agent))}\n",
            encoding="utf-8",
        )
        expected_name = "Saved display name"
    elif config_kind in ("malformed", "unreadable"):
        config_path.write_bytes(
            b'[chat\nname = "do not repair or overwrite"\n'
            if config_kind == "malformed"
            else b'[chat]\nname = "Unreadable saved name"\nworkspace = "@log"\n'
        )
        workspace = history

    before = _snapshot(logs_dir.parent)
    with monkeypatch.context() as read_patch:
        if config_kind == "unreadable":
            # Permission bits are unreliable under root/Windows. Fail the actual
            # file read, not ChatConfig itself, leaving recovery behavior real.
            original_read = Path.read_bytes

            def deny_config_read(path: Path) -> bytes:
                if path == config_path:
                    raise PermissionError("synthetic unreadable chat config")
                return original_read(path)

            read_patch.setattr(Path, "read_bytes", deny_config_read)
        item = _metadata(logs_dir, lookup, detail)
    _assert_history_metadata(item, history, workspace)
    assert item.name == expected_name
    if config_kind == "persisted":
        assert item.agent_name == "Metadata Bot"
        assert item.agent_path == str(agent)
        assert item.agent_avatar == "assets/avatar.png"
        assert item.agent_urls == {"repo": "https://example.org/metadata"}
    else:
        assert item.agent_name is None
        assert item.agent_path is None
    assert _snapshot(logs_dir.parent) == before


@pytest.mark.parametrize("config_kind", ["missing", "log"])
def test_chats_clean_dry_run_does_not_create_workspace(
    logs_dir: Path, config_kind: str
) -> None:
    history = _make_history(logs_dir)
    expected_name = history.name
    if config_kind == "log":
        (history / "config.toml").write_text(
            '[chat]\nname = "Abandoned session"\nworkspace = "@log"\n', encoding="utf-8"
        )
        expected_name = "Abandoned session"
    before = _snapshot(logs_dir.parent)
    result = CliRunner().invoke(chats, ["clean", "--json"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["found"] == 1
    assert output["deleted"] == 0
    assert output["freed_bytes"] == 0
    assert len(output["conversations"]) == 1
    row = output["conversations"][0]
    assert row["id"] == history.name
    assert row["name"] == expected_name
    assert row["messages"] == 1
    expected_size = sum(
        len(value)
        for path, (kind, value) in before.items()
        if path.startswith(f"logs/{history.name}/")
        and kind == "file"
        and isinstance(value, bytes)
    )
    assert row["size_bytes"] == expected_size
    assert output["total_bytes"] == expected_size
    assert _snapshot(logs_dir.parent) == before


@pytest.mark.parametrize("lookup", ["list", "direct"])
def test_metadata_does_not_resurrect_removed_legacy_history(
    logs_dir: Path, monkeypatch: pytest.MonkeyPatch, lookup: str, detail: bool
) -> None:
    history = _make_history(logs_dir)
    original_load = ChatConfig.from_logdir
    removed = False

    def remove_before_config_load(path: Path, **kwargs: Any) -> ChatConfig:
        nonlocal removed
        assert path == history
        shutil.rmtree(history)
        removed = True
        return original_load(path, **kwargs)

    # This boundary follows all JSONL reads/stat calls, avoiding an unrelated
    # missing-file race while proving config loading cannot recreate a logdir.
    monkeypatch.setattr(ChatConfig, "from_logdir", remove_before_config_load)
    item = _metadata(logs_dir, lookup, detail)
    assert removed
    _assert_history_metadata(item, history, history / "workspace")
    assert item.name == history.name
    assert not history.exists(), "metadata loading resurrected a deleted history"
    assert _snapshot(logs_dir) == {}
