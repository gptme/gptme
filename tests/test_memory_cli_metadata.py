"""Harness wrappers must retain provenance through the public save command."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.util import main as util_main
from gptme.memory import MemoryRoot, MemoryStore


def test_cli_metadata_preserves_other_fields_and_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    store = MemoryStore([MemoryRoot("explicit", tmp_path)])
    store.save(
        "kept", "Original", title="Curated label", metadata={"source": "operator"}
    )
    policy = tmp_path / ".memory-index.json"
    policy.write_text(
        json.dumps({"version": 1, "budget": 1000, "selected": ["kept.md"]})
    )
    before_policy = policy.read_bytes()
    result = CliRunner().invoke(
        util_main,
        [
            "memory",
            "save",
            "kept",
            "Updated",
            "--scope",
            "explicit",
            "--type",
            "feedback",
            "--metadata",
            '{"originSessionId":"session-123","node_type":"memory"}',
        ],
        input="Body with provenance",
    )
    assert result.exit_code == 0, result.output
    entry = store.get("kept")
    assert entry is not None
    assert entry.metadata["source"] == "operator"
    assert entry.metadata["originSessionId"] == "session-123"
    assert entry.title == "Curated label"
    assert entry.type == "feedback"
    assert entry.body.strip() == "Body with provenance"
    assert policy.read_bytes() == before_policy
    assert store.check_index()


@pytest.mark.parametrize("metadata", ["[]", "null", '"text"', "123", "{broken"])
def test_invalid_metadata_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: str
) -> None:
    root = tmp_path / "new-root"
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(root))
    result = CliRunner().invoke(
        util_main,
        ["memory", "save", "entry", "Description", "--metadata", metadata],
        input="Body",
    )
    assert result.exit_code != 0
    assert "--metadata" in result.output and "JSON object" in result.output
    assert not root.exists()
