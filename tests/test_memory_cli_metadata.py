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


def test_metadata_cannot_override_canonical_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    store = MemoryStore([MemoryRoot("explicit", tmp_path)])
    path = store.save("kept", "Original", type="feedback")
    before = path.read_bytes()
    result = CliRunner().invoke(
        util_main,
        [
            "memory",
            "save",
            "kept",
            "Changed",
            "--type",
            "feedback",
            "--metadata",
            '{"type":"project"}',
        ],
        input="Changed body",
    )
    assert result.exit_code != 0
    assert "--type" in result.output
    assert path.read_bytes() == before


def test_save_over_malformed_existing_entry_shows_friendly_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI must not traceback when the existing entry has malformed YAML frontmatter.

    store.save() calls parse_entry(..., strict=True) on the existing path and
    raises MemoryFrontmatterError (a MemoryParseError subclass, not ValueError).
    Without the fix, the CLI's except clause silently misses it and the process
    exits with a raw traceback.
    """
    monkeypatch.setenv("GPTME_MEMORY_DIRS", str(tmp_path))
    broken = tmp_path / "broken-entry.md"
    broken.write_text(
        "---\nname: broken-entry\ntype: [invalid yaml: unclosed\n---\n\nBody.\n"
    )
    result = CliRunner().invoke(
        util_main,
        ["memory", "save", "broken-entry", "New description"],
        input="New body",
    )
    assert result.exit_code != 0
    # Must be a friendly error message, not a raw Python traceback
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "MemoryFrontmatterError" not in (result.output or "")
    assert "Traceback" not in (result.output or "")
