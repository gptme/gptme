"""Tests for the durable prompt queue (`gptme.prompt_queue`).

Focuses on how ``drain_prompt_queue`` handles malformed lines: a truncated or
corrupted JSONL line (e.g. a half-written record left by a crash mid-append)
must survive a drain cycle rather than being silently deleted.
"""

import json
from pathlib import Path

import pytest

from gptme.prompt_queue import (
    drain_prompt_queue,
    drain_steer_prompts,
    get_prompt_queue_path,
    queue_prompt,
)


def _write_malformed_line(logdir, raw: str) -> None:
    """Append a raw (non-JSON) line directly to the queue file."""
    queue_path = get_prompt_queue_path(logdir)
    with queue_path.open("a", encoding="utf-8") as f:
        f.write(raw + "\n")


def test_drain_preserves_malformed_line(tmp_path):
    """A malformed queue line survives a drain instead of being dropped.

    Regression: ``drain_prompt_queue`` used to ``continue`` past a
    ``JSONDecodeError`` without re-appending the offending line to
    ``remaining``, so the line was removed from the queue file and lost
    forever on the next drain.
    """
    logdir = tmp_path / "preserve-malformed"
    logdir.mkdir()

    queue_prompt(logdir, "valid prompt")
    _write_malformed_line(logdir, '{"content": "truncated')  # truncated JSON

    drained = drain_prompt_queue(logdir)

    # The valid record is drained normally.
    assert len(drained) == 1
    assert "valid prompt" in drained[0].content

    # The malformed line is preserved on disk for inspection / retry.
    queue_path = get_prompt_queue_path(logdir)
    assert queue_path.exists(), "malformed line was dropped instead of preserved"
    remaining = queue_path.read_text(encoding="utf-8")
    assert '{"content": "truncated' in remaining


def test_drain_preserves_malformed_line_across_drains(tmp_path):
    """The preserved malformed line is stable across repeated drains."""
    logdir = tmp_path / "preserve-across-drains"
    logdir.mkdir()

    _write_malformed_line(logdir, '{"content": "truncated')

    assert drain_prompt_queue(logdir) == []
    assert drain_prompt_queue(logdir) == []

    queue_path = get_prompt_queue_path(logdir)
    assert queue_path.exists()
    assert '{"content": "truncated' in queue_path.read_text(encoding="utf-8")


def test_skill_invocation_metadata_roundtrip(tmp_path: Path) -> None:
    queue_prompt(tmp_path, "skill prompt", skill_invocation_id="invocation-123")

    record = json.loads(get_prompt_queue_path(tmp_path).read_text(encoding="utf-8"))
    assert record["content"] == "skill prompt"
    assert record["skill_invocation_id"] == "invocation-123"

    (message,) = drain_prompt_queue(tmp_path)
    assert message.content == "skill prompt"
    assert message.role == "user"
    assert message.quiet is True
    assert message.metadata == {"skill_invocation_id": "invocation-123"}
    assert message.to_dict()["metadata"] == message.metadata


def test_prompt_without_skill_invocation_metadata(tmp_path: Path) -> None:
    queue_prompt(tmp_path, "regular prompt")

    record = json.loads(get_prompt_queue_path(tmp_path).read_text(encoding="utf-8"))
    assert "skill_invocation_id" not in record

    (message,) = drain_prompt_queue(tmp_path)
    assert message.content == "regular prompt"
    assert message.metadata is None
    assert "metadata" not in message.to_dict()


@pytest.mark.parametrize("invocation_id", [None, "", " \t", 123, True, [], {}])
def test_drain_ignores_malformed_skill_invocation_metadata(
    tmp_path: Path, invocation_id: object
) -> None:
    get_prompt_queue_path(tmp_path).write_text(
        json.dumps({"content": "valid prompt", "skill_invocation_id": invocation_id})
        + "\n",
        encoding="utf-8",
    )

    (message,) = drain_prompt_queue(tmp_path)
    assert message.content == "valid prompt"
    assert message.metadata is None
    assert not get_prompt_queue_path(tmp_path).exists()


def test_skill_invocation_metadata_survives_partial_drain(tmp_path: Path) -> None:
    queue_prompt(tmp_path, "first prompt")
    queue_prompt(tmp_path, "steering prompt", steer=True)
    queue_prompt(tmp_path, "skill prompt", skill_invocation_id="invocation-123")

    assert [
        message.content for message in drain_prompt_queue(tmp_path, max_items=1)
    ] == ["first prompt"]
    (skill_message,) = drain_prompt_queue(tmp_path)
    assert skill_message.content == "skill prompt"
    assert skill_message.metadata == {"skill_invocation_id": "invocation-123"}

    (steer_message,) = drain_steer_prompts(tmp_path)
    assert steer_message.content == "steering prompt"
    assert steer_message.metadata is None
    assert not get_prompt_queue_path(tmp_path).exists()
