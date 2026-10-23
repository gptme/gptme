"""A corrupt conversation.jsonl must degrade to skipped lines, not crash readers."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_resume import resume
from gptme.logmanager import Log, list_conversations

GOOD = b'{"role": "user", "content": "hello", "timestamp": "2026-10-05T10:00:00"}\n'

# Rows whose metadata is the wrong shape: valid JSON, so they pass the JSONL
# reader, but the listing scan must not call ``.get`` on them.
BAD_METADATA = (
    b'{"role": "system", "content": "note", "metadata": 5}\n'
    b'{"role": "system", "content": "note", "metadata": "oops"}\n'
    b'{"role": "system", "content": "note", "metadata": ["x"]}\n'
    b'{"role": "system", "content": "note", "metadata": {"usage": "oops"}}\n'
)


def _write(tmp_path: Path, *chunks: bytes) -> Path:
    conv = tmp_path / "conversation.jsonl"
    conv.write_bytes(b"".join(chunks))
    return conv


def test_read_jsonl_skips_unusable_lines(tmp_path: Path):
    conv = _write(
        tmp_path,
        b"[]\n",
        b"5\n",
        b'"str"\n',
        b'{"role": "user", "content": "x", "timestamp": "notadate"}\n',
        b'{"role": "user"}\n',
        b'{"role": 5, "content": ["x"]}\n',
        b'{"role": "assistant", "content": null}\n',
        GOOD,
    )
    messages = Log.read_jsonl(conv).messages
    assert [m.content for m in messages] == ["hello"]


def test_read_jsonl_survives_invalid_utf8(tmp_path: Path):
    conv = _write(tmp_path, b"\xff\xfe\x80\n", GOOD)
    messages = Log.read_jsonl(conv).messages
    assert [m.content for m in messages] == ["hello"]


def test_resume_list_survives_corrupt_session(tmp_path: Path, monkeypatch):
    logs = tmp_path / "logs"
    bad = logs / "2026-10-05-bad"
    bad.mkdir(parents=True)
    (bad / "conversation.jsonl").write_bytes(b"\xff\xfe\x80\n[]\n" + GOOD)
    (bad / "config.toml").write_bytes(b"\xff\xfe")
    monkeypatch.setattr("gptme.cli.cmd_resume._get_logs_dir", lambda: logs)
    for args in (["--list"], ["--session", str(bad)]):
        result = CliRunner().invoke(resume, args)
        assert result.exit_code == 0, (args, result.output, result.exception)


@pytest.mark.parametrize("large_file", [False, True])
def test_list_conversations_survives_corrupt_metadata(
    tmp_path: Path, monkeypatch, large_file: bool
):
    """A row with a non-object ``metadata``/``usage`` must be skipped.

    The full scan aggregates cost and token metadata, but a wrong-typed field
    used to abort the scan and with it every listing that reads this file —
    including ``gptme-util chats list`` and the recent-chats prompt. Above the
    8 KiB tail window the fast scan reads the same rows through a second code
    path, so both are exercised.
    """
    logs = tmp_path / "logs"
    conv = logs / "2026-10-05-badmeta" / "conversation.jsonl"
    conv.parent.mkdir(parents=True)
    padding = GOOD * 120 if large_file else b""
    # The corrupt rows come last so the tail scan meets them first.
    conv.write_bytes(padding + GOOD + BAD_METADATA)
    assert large_file == (conv.stat().st_size > 8192)
    monkeypatch.setattr("gptme.logmanager.conversations.get_logs_dir", lambda: logs)

    for detail in (True, False):
        convs = list_conversations(limit=5, include_test=True, detail=detail)
        assert [c.id for c in convs] == ["2026-10-05-badmeta"], detail
        assert convs[0].messages == (padding + GOOD + BAD_METADATA).count(b"\n")
