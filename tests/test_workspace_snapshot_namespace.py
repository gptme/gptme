"""Real Git namespace isolation for the shadow snapshot backend."""

import os
import subprocess
from pathlib import Path

import pytest

from gptme.util.git_cmd import GIT_CMD
from gptme.workspace_snapshot import Shadow, init_shadow, restore, snapshot, tree_hash


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ws = tmp_path / "workspace"
    ws.mkdir()
    git(ws, "init", "-q", "-b", "fixture")
    git(ws, "config", "user.name", "Fixture")
    git(ws, "config", "user.email", "fixture@example.invalid")
    (ws / "a").write_text("committed\n")
    git(ws, "add", "a")
    git(ws, "commit", "-qm", "test: fixture")
    (ws / "a").write_text("staged\n")
    git(ws, "add", "a")
    (ws / "a").write_text("unstaged\n")
    (ws / "new").write_text("untracked\n")
    return ws


def git(ws: Path, *args: str) -> str:
    return subprocess.run(
        [GIT_CMD, "-C", str(ws), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()


@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.parametrize(
    "route",
    [
        "index",
        "alternate-index",
        "objects",
        "common",
        "directory",
        "alternates",
        "namespace",
        "worktree",
        "shallow",
        "graft",
        "replace",
    ],
)
def test_repository_routes_cannot_escape_shadow(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initialized: bool,
    route: str,
) -> None:
    shadow = init_shadow(workspace) if initialized else Shadow.for_workspace(workspace)
    index = workspace / ".git/index"
    before = index.read_bytes()
    head = git(workspace, "rev-parse", "HEAD")
    alternate = tmp_path / "alternate-index"
    alternate.write_bytes(before)
    object_dir = tmp_path / "foreign-objects"
    object_dir.mkdir()
    values = {
        "index": ("GIT_INDEX_FILE", str(index)),
        "alternate-index": ("GIT_INDEX_FILE", str(alternate)),
        "objects": ("GIT_OBJECT_DIRECTORY", str(object_dir)),
        "common": ("GIT_COMMON_DIR", str(workspace / ".git")),
        "directory": ("GIT_DIR", str(workspace / ".git")),
        "alternates": (
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            str(workspace / ".git/objects"),
        ),
        "namespace": ("GIT_NAMESPACE", "foreign"),
        "worktree": ("GIT_WORK_TREE", str(tmp_path / "missing-worktree")),
        "shallow": ("GIT_SHALLOW_FILE", str(tmp_path / "missing-shallow")),
        "graft": ("GIT_GRAFT_FILE", str(tmp_path / "missing-grafts")),
        "replace": ("GIT_REPLACE_REF_BASE", "refs/foreign-replace/"),
    }
    name, value = values[route]
    with monkeypatch.context() as inherited:
        inherited.setenv(name, value)
        shadow = init_shadow(workspace)
        sha = snapshot(shadow, label="isolated")
        assert sha
        assert tree_hash(shadow)
        (workspace / "a").write_text("later\n")
        assert restore(shadow, sha)
    assert index.read_bytes() == before
    assert alternate.read_bytes() == before
    assert not list(object_dir.iterdir()), "objects escaped shadow storage"
    assert git(workspace, "rev-parse", "HEAD") == head
    assert git(workspace, "show", ":a") == "staged"
    assert (workspace / "a").read_text() == "unstaged\n"
    assert (workspace / "new").read_text() == "untracked\n"
    # No dependency on inherited alternates: validate independently afterward.
    assert shadow.run("fsck", "--no-reflogs").returncode == 0


def test_call_overrides_cannot_redirect_repository(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = init_shadow(workspace)
    index = workspace / ".git/index"
    before = index.read_bytes()
    shadow.run("add", "-A", env={"GIT_INDEX_FILE": str(index)})
    assert index.read_bytes() == before
    assert shadow.run(
        "rev-parse", "--git-dir", env={"GIT_DIR": str(workspace / ".git")}
    ).stdout.strip() == str(shadow.git_dir)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Intended Identity")
    assert shadow.env()["GIT_AUTHOR_NAME"] == "Intended Identity"
    assert shadow.env()["GIT_CONFIG_GLOBAL"] == os.devnull
    assert shadow.run(
        "var", "GIT_AUTHOR_IDENT", env={"GIT_AUTHOR_NAME": "Call Identity"}
    ).stdout.startswith("Call Identity <")


def test_configuration_and_commit_identity_remain_supported(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "intended.gitconfig"
    config.write_text("[snapshot-test]\n    marker = inherited-config\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Configured Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2020-01-02T03:04:05+00:00")
    shadow = init_shadow(workspace)
    assert (
        shadow.run("config", "--get", "snapshot-test.marker").stdout.strip()
        == "inherited-config"
    )
    # Git versions differ in whether strict ISO UTC is rendered as Z or +00:00.
    assert shadow.run("log", "-1", "--format=%an|%ae|%at").stdout.strip() == (
        "Configured Author|author@example.invalid|1577934245"
    )
    assert (
        shadow.run(
            "config",
            "--get",
            "snapshot-test.marker",
            env={
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "snapshot-test.marker",
                "GIT_CONFIG_VALUE_0": "call-config",
            },
        ).stdout.strip()
        == "call-config"
    )


def test_initialization_config_writes_are_shadow_local(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = workspace / ".git/config"
    before = config.read_bytes()
    monkeypatch.setenv("GIT_CONFIG", str(config))
    shadow = init_shadow(workspace)
    assert config.read_bytes() == before
    assert shadow.env()["GIT_CONFIG"] == str(config)
    monkeypatch.delenv("GIT_CONFIG")
    assert (
        shadow.run("config", "--local", "--get", "core.bare").stdout.strip() == "false"
    )
    assert (
        shadow.run("config", "--local", "--get", "user.email").stdout.strip()
        == "snapshot@gptme.local"
    )


@pytest.mark.parametrize(
    "name",
    [
        "GIT_NAMESPACE",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_GRAFT_FILE",
        "GIT_SHALLOW_FILE",
        "GIT_REPLACE_REF_BASE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_PREFIX",
    ],
)
def test_secondary_routes_removed_at_environment_boundary(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    shadow = Shadow.for_workspace(workspace)
    monkeypatch.setenv(name, "foreign-route")
    assert name not in shadow.env()
    assert name not in shadow.env({name: "call-route"})
