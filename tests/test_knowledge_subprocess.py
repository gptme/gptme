"""Protect outside stores when the knowledge CLI runs in a real subprocess."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("outside_checkout", [False, True])
@pytest.mark.parametrize("explicit_source", [False, True])
def test_knowledge_cli_subprocess_isolation(
    tmp_path: Path, outside_checkout: bool, explicit_source: bool
) -> None:
    checkout = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cwd = workspace if outside_checkout else checkout
    fake_home = tmp_path / "home"
    protected = fake_home / ".local/share/gptme/knowledge/entries.jsonl"
    protected.parent.mkdir(parents=True)
    outside_entry = {
        "id": "00000000-0000-4000-8000-000000000001",
        "problem": "outside telescope calibration",
        "resolution": "retain original calibration",
        "tags": [],
        "keywords": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "memory_type": "knowledge_entry",
    }
    original = (json.dumps(outside_entry) + "\n").encode()
    protected.write_bytes(original)
    data_home = tmp_path / "isolated-data"
    memory_dir = tmp_path / "memory"
    # An explicit interpreter and source path work even outside the checkout.
    # Do not inherit user config, credentials, agent roots, or indexing binaries.
    env = {
        "HOME": str(fake_home),
        "USERPROFILE": str(fake_home),
        "XDG_DATA_HOME": str(data_home),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "GPTME_MEMORY_DIRS": str(memory_dir),
        "PYTHONPATH": str(checkout),
        "PATH": "",
        "PYTHONIOENCODING": "utf-8",
    }
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    def run(*args: str, outside_store: bool = False) -> str:
        child_env = env.copy()
        if outside_store:
            child_env.pop("XDG_DATA_HOME")
        result = subprocess.run(
            [sys.executable, "-m", "gptme.cli.util", *args],
            cwd=cwd,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert protected.read_bytes() == original
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    saved = json.loads(
        run(
            "knowledge",
            "save",
            "isolated pytest discovery",
            "prefix functions with test_",
            "--tag",
            "pytest",
            "--json",
        )
    )
    isolated_store = data_home / "gptme/knowledge/entries.jsonl"
    assert [json.loads(line) for line in isolated_store.read_text().splitlines()] == [
        saved
    ]
    assert not (isolated_store.parent / "rag").exists()
    assert json.loads(run("knowledge", "search", "pytest discovery", "--json")) == [
        saved
    ]
    assert json.loads(run("knowledge", "search", "telescope", "--json")) == []

    before_migration = isolated_store.read_bytes()
    source = [str(isolated_store)] if explicit_source else []
    dry_run = run("memory", "migrate-knowledge-jsonl", *source, "--dry-run")
    assert "would migrate 1" in dry_run
    assert "isolated-pytest-discovery" in dry_run
    assert "outside-telescope" not in dry_run
    assert isolated_store.read_bytes() == before_migration
    assert not list(tmp_path.rglob("*.md"))
    assert json.loads(run("memory", "list", "--json")) == []

    # The fake default store is readable, but never receives the isolated write.
    assert (
        json.loads(
            run("knowledge", "search", "pytest discovery", "--json", outside_store=True)
        )
        == []
    )
    assert json.loads(
        run("knowledge", "search", "telescope", "--json", outside_store=True)
    ) == [outside_entry]
