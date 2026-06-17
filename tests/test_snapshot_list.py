"""Tests for list_snapshots_rich and gptme-util snapshot list."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gptme.cli.util import main
from gptme.workspace_snapshot import (
    init_shadow,
    list_snapshots_rich,
    snapshot,
)


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


@pytest.fixture
def workspace(tmp_path, isolated_state_dir):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("hello\n")
    return ws


# ── list_snapshots_rich unit tests ───────────────────────────────────────────


def test_list_snapshots_rich_empty_on_uninitialised(workspace):
    from gptme.workspace_snapshot import Shadow

    shadow = Shadow.for_workspace(workspace)
    assert list_snapshots_rich(shadow) == []


def test_list_snapshots_rich_initial(workspace):
    shadow = init_shadow(workspace)
    entries = list_snapshots_rich(shadow)
    assert len(entries) == 1
    e = entries[0]
    assert e["sha"]
    assert e["label"] == "initial"
    assert e["timestamp"] > 0
    assert e["n_msgs"] is None


def test_list_snapshots_rich_with_n_msgs(workspace):
    shadow = init_shadow(workspace)
    (workspace / "a.txt").write_text("v1\n")
    snapshot(shadow, label="step1", n_msgs=5)

    entries = list_snapshots_rich(shadow)
    # newest first — step1 is at index 0
    assert entries[0]["label"] == "step1"
    assert entries[0]["n_msgs"] == 5


def test_list_snapshots_rich_without_n_msgs(workspace):
    shadow = init_shadow(workspace)
    (workspace / "a.txt").write_text("v1\n")
    snapshot(shadow, label="plain-label")

    entries = list_snapshots_rich(shadow)
    assert entries[0]["label"] == "plain-label"
    assert entries[0]["n_msgs"] is None


def test_list_snapshots_rich_limit(workspace):
    shadow = init_shadow(workspace)
    for i in range(5):
        (workspace / f"f{i}.txt").write_text(f"{i}\n")
        snapshot(shadow, label=f"snap{i}")

    entries = list_snapshots_rich(shadow, limit=3)
    assert len(entries) == 3
    # newest first
    assert entries[0]["label"] == "snap4"


def test_list_snapshots_rich_multiple_newest_first(workspace):
    shadow = init_shadow(workspace)
    for i in range(3):
        (workspace / f"f{i}.txt").write_text(f"{i}\n")
        snapshot(shadow, label=f"s{i}")

    entries = list_snapshots_rich(shadow, limit=100)
    labels = [e["label"] for e in entries]
    assert labels[0] == "s2"
    assert labels[-1] == "initial"


# ── CLI tests (gptme-util snapshot list) ─────────────────────────────────────


def test_cli_list_empty_workspace_no_error(workspace, monkeypatch):
    """No snapshots → empty output, exit 0."""
    runner = CliRunner()
    result = runner.invoke(main, ["snapshot", "list", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_cli_list_shows_table(workspace):
    shadow = init_shadow(workspace)
    (workspace / "a.txt").write_text("x\n")
    snapshot(shadow, label="my-snap", n_msgs=3)

    runner = CliRunner()
    result = runner.invoke(main, ["snapshot", "list", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "my-snap" in result.output
    assert "SHA" in result.output


def test_cli_list_json_empty_workspace(workspace, monkeypatch):
    """--json with no snapshots → valid empty JSON array, exit 0."""
    runner = CliRunner()
    result = runner.invoke(
        main, ["snapshot", "list", "--json", "--workspace", str(workspace)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == []


def test_cli_list_json_with_snapshots(workspace):
    shadow = init_shadow(workspace)
    (workspace / "a.txt").write_text("x\n")
    snapshot(shadow, label="s1", n_msgs=7)

    runner = CliRunner()
    result = runner.invoke(
        main, ["snapshot", "list", "--json", "--workspace", str(workspace)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert any(e["label"] == "s1" for e in data)
    s1 = next(e for e in data if e["label"] == "s1")
    assert s1["n_msgs"] == 7
    assert s1["timestamp"] > 0
    assert s1["sha"]


def test_cli_list_json_no_n_msgs(workspace):
    shadow = init_shadow(workspace)
    (workspace / "a.txt").write_text("x\n")
    snapshot(shadow, label="bare")

    runner = CliRunner()
    result = runner.invoke(
        main, ["snapshot", "list", "--json", "--workspace", str(workspace)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    bare = next(e for e in data if e["label"] == "bare")
    assert bare["n_msgs"] is None


def test_cli_list_limit(workspace):
    shadow = init_shadow(workspace)
    for i in range(4):
        (workspace / f"f{i}.txt").write_text(f"{i}\n")
        snapshot(shadow, label=f"snap{i}")

    runner = CliRunner()
    result = runner.invoke(
        main, ["snapshot", "list", "--json", "-n", "2", "--workspace", str(workspace)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 2
    assert data[0]["label"] == "snap3"
