"""A prompt that cannot be passed to a child must not abort the batch."""

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.cli import cmd_batch


@pytest.mark.skipif(
    sys.platform not in ("darwin", "linux"),
    reason="Uses a POSIX test executable and the host's exec argument limit",
)
def test_batch_continues_after_oversized_argument(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    child = tmp_path / "gptme-child"
    child.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    child.chmod(0o700)
    monkeypatch.setattr(cmd_batch.sys, "executable", str(child))

    result = CliRunner().invoke(
        cmd_batch.batch_cmd,
        ["--jsonl-only"],
        input="x" * (4 * 1024 * 1024) + "\nshort prompt\n",
    )

    assert result.exit_code == 0, repr(result.exception)
    failed, done = [json.loads(line) for line in result.stdout.splitlines()]
    assert (failed["index"], failed["exit_reason"], failed["returncode"]) == (
        0,
        "error",
        None,
    )
    assert (done["index"], done["prompt"], done["exit_reason"]) == (
        1,
        "short prompt",
        "done",
    )
