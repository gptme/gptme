"""tmux pane output is bounded and spills to the conversation's tool-outputs (#4663)."""

import shutil
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gptme.tools import tmux
from gptme.tools.tmux import (
    MAX_OUTPUT_CHARS,
    _truncate_output,
    execute_tmux,
    inspect_pane,
    new_session,
    send_keys,
    wait_for_output,
)

_CURRENT_LOG = "gptme.logmanager.LogManager.get_current_log"


def _saved(tmp_path):
    root = tmp_path / "tool-outputs"
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def _long_pane(n: int = 500) -> str:
    return "\n".join(f"line-{i}" for i in range(n))


def test_short_output_is_unchanged_and_not_saved(tmp_path):
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        assert _truncate_output("hello\nworld") == "hello\nworld"
    assert _saved(tmp_path) == []


def test_line_truncation_saves_to_conversation_tool_outputs(tmp_path):
    content = _long_pane()
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        preview = _truncate_output(content, context="inspect-pane gptme_1")

    files = _saved(tmp_path)
    assert len(files) == 1
    assert files[0].parent == tmp_path / "tool-outputs" / "tmux"
    assert files[0].read_text() == content
    assert "300 lines truncated" in preview
    assert f"full captured scrollback saved to {files[0]}" in preview
    assert preview.startswith("line-0\n") and preview.endswith("line-499")


def test_no_conversation_means_no_spill_and_no_cache_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with patch(_CURRENT_LOG, return_value=None):
        preview = _truncate_output(_long_pane())
    assert "300 lines truncated" in preview
    assert "saved to" not in preview
    assert not (tmp_path / "cache").exists()


def test_single_huge_line_is_bounded_by_characters(tmp_path):
    content = "HEAD" + "x" * (2 * 1024 * 1024) + "TAIL"
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        preview = _truncate_output(content)

    assert preview.startswith("HEAD") and preview.endswith("TAIL")
    assert len(preview) < MAX_OUTPUT_CHARS + 500
    assert f"{len(content) - MAX_OUTPUT_CHARS} characters truncated" in preview
    files = _saved(tmp_path)
    assert len(files) == 1 and files[0].read_text() == content


def test_many_wide_lines_hit_both_bounds(tmp_path):
    lines = [f"L{i}-" + "y" * 2000 for i in range(400)]
    content = "\n".join(lines)
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        preview = _truncate_output(content)

    assert len(preview) < MAX_OUTPUT_CHARS + 500
    assert "200 lines and " in preview and "characters truncated" in preview
    assert preview.startswith("L0-") and preview.rstrip().endswith("y")
    assert "L399-" in preview  # most recent output stays visible
    assert len(_saved(tmp_path)) == 1


def test_double_bound_keeps_marker_between_distant_regions(tmp_path):
    head = [f"HEAD-{i}" for i in range(50)]
    tail = [f"TAIL-{i}-" + "y" * 1000 for i in range(150)]
    content = "\n".join(head + ["OMITTED"] * 200 + tail)
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        preview = _truncate_output(content)

    before = preview.split("\n... (", 1)[0]
    assert before == "\n".join(head)
    assert "TAIL-" not in before
    assert "200 lines and " in preview
    assert preview.endswith(tail[-1])
    assert len(preview) < MAX_OUTPUT_CHARS + 500
    assert _saved(tmp_path)[0].read_text() == content


def test_marker_only_overflow_does_not_duplicate_text(tmp_path):
    # 300 lines whose kept 200 lines fit exactly under the char cap.
    content = "\n".join("z" * 9 for _ in range(300))
    with patch(_CURRENT_LOG, return_value=None):
        preview = _truncate_output(content, max_chars=len("\n".join(["z" * 9] * 200)))
    assert "100 lines truncated" in preview
    assert "characters truncated" not in preview
    assert preview.count("z" * 9) == 200


def test_save_failure_still_bounds(tmp_path):
    with (
        patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)),
        patch.object(tmux, "save_large_output", side_effect=OSError("disk full")),
    ):
        preview = _truncate_output(_long_pane())
    assert "300 lines truncated" in preview
    assert "saved to" not in preview


def test_every_pane_reading_command_spills_to_the_conversation(tmp_path):
    """new-session/send-keys previously had no spill dir; inspect/wait used the
    user cache root. All four must land in the conversation's tool-outputs."""
    content = _long_pane()
    with (
        patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)),
        patch.object(tmux, "_capture_pane", return_value=content),
        patch.object(tmux, "_run_tmux_command"),
        patch.object(tmux, "_run_tmux_command_with_retry"),
        patch.object(tmux, "get_sessions", return_value=[]),
        patch.object(tmux, "sleep"),
        patch.object(
            tmux.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ),
    ):
        msgs = [
            new_session("echo hi"),
            send_keys("gptme_1", "ls Enter"),
            inspect_pane("gptme_1"),
            wait_for_output("gptme_1", timeout=0, stable_time=0),
        ]

    for msg in msgs:
        assert "full captured scrollback saved to" in msg.content, msg.content[:200]
    files = _saved(tmp_path)
    assert files, "expected spill files under tool-outputs/tmux"
    assert all(f.parent == tmp_path / "tool-outputs" / "tmux" for f in files)
    assert all(f.read_text() == content for f in files)


def test_execute_tmux_inspect_does_not_write_user_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with (
        patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path / "conv")),
        patch.object(tmux, "_capture_pane", return_value=_long_pane()),
        patch.object(
            tmux,
            "get_confirmation",
            return_value=SimpleNamespace(action=tmux.ConfirmAction.CONFIRM),
        ),
    ):
        msgs = list(execute_tmux("inspect-pane gptme_1", [], None))

    assert len(msgs) == 1
    assert "full captured scrollback saved to" in msgs[0].content
    assert not (tmp_path / "cache").exists()
    assert (tmp_path / "conv" / "tool-outputs" / "tmux").is_dir()


def test_pane_credentials_are_redacted_before_save_and_preview(tmp_path):
    secret = "sk-ant-api03-" + "A1b2C3d4" * 6
    content = f"ANTHROPIC_API_KEY={secret}\n" + _long_pane()
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        preview = _truncate_output(content, context="inspect-pane gptme_1")

    (saved,) = _saved(tmp_path)
    assert secret not in saved.read_text()
    assert secret not in preview
    assert "line-499" in saved.read_text()  # the rest is retained


def test_short_pane_credentials_are_redacted_too(tmp_path):
    secret = "sk-ant-api03-" + "A1b2C3d4" * 6
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        out = _truncate_output(f"ANTHROPIC_API_KEY={secret}\nok")
    assert secret not in out and out.endswith("ok")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not available")
@pytest.mark.parametrize("max_chars", [MAX_OUTPUT_CHARS, 120])
def test_wrapped_credentials_are_redacted_after_real_capture(
    tmp_path, monkeypatch, max_chars
):
    """Join soft wraps, not real newlines, before redacting previews and spills."""
    secret = "sk-ant-api03-" + "A1b2C3d4" * 40
    payload = tmp_path / "payload"
    payload.write_text(
        f"ANTHROPIC_API_KEY={secret}\n"
        + "ordinary output\n" * 20
        + "first\nsecond\nCAPTURE_READY\n"
    )
    # A private socket keeps this test away from users' and parallel tests' panes.
    prefix = ["tmux", "-S", str(tmp_path / "socket")]
    run = subprocess.run

    def isolated_run(cmd, **kwargs):
        assert cmd[0] == "tmux"
        return run(prefix + cmd[1:], **kwargs)

    monkeypatch.setattr(tmux.subprocess, "run", isolated_run)
    run(
        prefix
        + [
            "new-session",
            "-d",
            "-s",
            "wrap",
            "-x",
            "120",
            "-y",
            "40",
            f"cat {payload}; sleep 30",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            captured = tmux._capture_pane("wrap")
            if "CAPTURE_READY" in captured:
                break
            assert time.monotonic() < deadline, "pane did not finish printing"
            time.sleep(0.02)

        preview = _truncate_output(captured, logdir=tmp_path, max_chars=max_chars)
        assert "A1b2C3d4" not in preview
        assert "[REDACTED]" in preview
        files = _saved(tmp_path)
        if max_chars == 120:
            (saved,) = files
            assert "A1b2C3d4" not in saved.read_text()
            assert "[REDACTED]" in saved.read_text()
            assert "first\nsecond\nCAPTURE_READY" in saved.read_text()
        else:
            assert not files
            assert "first\nsecond\nCAPTURE_READY" in preview
        assert f"ANTHROPIC_API_KEY={secret}\n" in captured
    finally:
        run(prefix + ["kill-server"], check=False, capture_output=True, timeout=10)


def test_pane_redaction_respects_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("GPTME_SHELL_REDACT_SECRETS", "0")
    secret = "sk-ant-api03-" + "A1b2C3d4" * 6
    with patch(_CURRENT_LOG, return_value=SimpleNamespace(logdir=tmp_path)):
        out = _truncate_output(f"ANTHROPIC_API_KEY={secret}\nok")
    assert secret in out
