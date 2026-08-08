"""Tests for the durable prompt queue (`gptme.prompt_queue`).

Focuses on how ``drain_prompt_queue`` handles malformed lines: a truncated or
corrupted JSONL line (e.g. a half-written record left by a crash mid-append)
must survive a drain cycle rather than being silently deleted.
"""

from gptme.prompt_queue import (
    drain_prompt_queue,
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
