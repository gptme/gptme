"""A corrupt conversation.jsonl must degrade to skipped lines, not crash readers."""

from pathlib import Path

from click.testing import CliRunner

from gptme.cli.cmd_resume import resume
from gptme.logmanager import Log

GOOD = b'{"role": "user", "content": "hello", "timestamp": "2026-10-05T10:00:00"}\n'


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
