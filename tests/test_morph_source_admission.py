"""Source admission must finish before a Morph read or provider request."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks import ConfirmationResult
from gptme.tools import morph


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
@pytest.mark.parametrize("entry", ["execute", "preview", "apply"])
@pytest.mark.parametrize("symlink", [False, True])
def test_fifo_rejected_before_open(tmp_path: Path, entry: str, symlink: bool):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    target = tmp_path / "link" if symlink else fifo
    if symlink:
        try:
            target.symlink_to(fifo)
        except OSError:
            pytest.skip("symlink creation unavailable")
    # Use a child so a regression blocks for at most eight seconds. The actual
    # FIFO open is not mocked; run() kills/reaps a timed-out child.
    code = """
import sys
from pathlib import Path
from unittest.mock import patch
from gptme.tools import morph
path = Path(sys.argv[1])
with patch.object(morph, '_chat_complete', side_effect=AssertionError('inference before admission')) as api:
    if sys.argv[2] == 'execute':
        result = '\\n'.join(m.content for m in morph.execute_morph('edit', [str(path)], None))
    elif sys.argv[2] == 'preview':
        result = morph.preview_morph('new', path)
    else:
        result = '\\n'.join(m.content for m in morph.execute_morph_impl('new', path, 'old'))
    assert 'not a regular file' in result.lower(), result
    api.assert_not_called()
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(target), entry],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{entry} blocked opening a FIFO before source admission")
    assert result.returncode == 0, result.stdout + result.stderr
    assert fifo.is_fifo()


@pytest.mark.parametrize("entry", ["execute", "preview", "apply"])
def test_directory_rejected_before_open(tmp_path: Path, entry: str):
    with (
        patch("builtins.open", side_effect=AssertionError("opened nonregular source")),
        patch.object(morph, "_chat_complete") as api,
    ):
        result: str | None
        if entry == "execute":
            result = "\n".join(
                m.content for m in morph.execute_morph("edit", [str(tmp_path)], None)
            )
        elif entry == "preview":
            result = morph.preview_morph("new", tmp_path)
        else:
            result = "\n".join(
                m.content for m in morph.execute_morph_impl("new", tmp_path, "old")
            )
    assert result is not None and "not a regular file" in result.lower()
    api.assert_not_called()


@pytest.mark.parametrize("symlink", [False, True])
def test_regular_file_and_symlink_still_work(tmp_path: Path, symlink: bool):
    file = tmp_path / "file"
    file.write_text("old", encoding="utf-8")
    target = tmp_path / "link" if symlink else file
    if symlink:
        try:
            target.symlink_to(file)
        except OSError:
            pytest.skip("symlink creation unavailable")
    assert "-old" in (morph.preview_morph("new", target) or "")
    with (
        patch.object(morph, "_chat_complete", return_value=("new", None)) as api,
        patch(
            "gptme.hooks.get_confirmation", return_value=ConfirmationResult.confirm()
        ),
    ):
        messages = list(morph.execute_morph("edit", [str(target)], None))
    api.assert_called_once()
    assert any("successfully applied" in m.content for m in messages)
    assert file.read_text(encoding="utf-8") == "new"
    if symlink:
        assert target.is_symlink()


def test_missing_file_retains_error_without_inference(tmp_path: Path):
    target = tmp_path / "missing"
    with patch.object(morph, "_chat_complete") as api:
        messages = list(morph.execute_morph("edit", [str(target)], None))
    api.assert_not_called()
    assert "File not found" in messages[0].content
    assert morph.preview_morph("new", target) == "File does not exist"
    assert not target.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_confirmation_swap_rejected_before_apply_read(tmp_path: Path):
    target = tmp_path / "file"
    target.write_text("old", encoding="utf-8")

    def swap(**kwargs):
        target.unlink()
        os.mkfifo(target)
        return ConfirmationResult.confirm()

    # The real read of the original and preview still runs; only forbid reads
    # after the confirmation callback replaces it with a FIFO.
    real_open = open

    def guarded_open(*args, **kwargs):
        assert not target.is_fifo(), "opened FIFO after confirmation"
        return real_open(*args, **kwargs)

    with (
        patch.object(morph, "_chat_complete", return_value=("new", None)),
        patch("gptme.hooks.get_confirmation", side_effect=swap),
        patch("builtins.open", side_effect=guarded_open),
    ):
        messages = list(morph.execute_morph("edit", [str(target)], None))
    assert any("not a regular file" in m.content for m in messages)
    assert target.is_fifo()
