"""Subscription OAuth token files are written privately and atomically.

Regression for #5218 item 6: ``write_text`` then ``chmod`` left the refresh
token at 0644 until the chmod ran, and a failed write truncated the previous
login after the server-side refresh token was already spent.
"""

import json
import os
import stat
from pathlib import Path

import pytest

from gptme.llm import llm_grok_subscription as grok
from gptme.llm import llm_openai_subscription as oai


def _oai_auth(tag: str):
    return oai.SubscriptionAuth(
        access_token=f"access-{tag}",
        refresh_token=f"refresh-{tag}",
        account_id="acct",
        expires_at=9_999_999_999.0,
    )


def _grok_auth(tag: str):
    return grok.SubscriptionAuth(
        access_token=f"access-{tag}",
        refresh_token=f"refresh-{tag}",
        expires_at=9_999_999_999.0,
    )


CASES = [
    pytest.param(oai, _oai_auth, "openai_subscription.json", id="openai"),
    pytest.param(grok, _grok_auth, "grok_subscription.json", id="grok"),
]


def _token_dir(tmp_path):
    return tmp_path / "gptme" / "oauth"


@pytest.mark.parametrize(("mod", "make", "fname"), CASES)
def test_token_bytes_are_never_wider_than_0600(tmp_path, monkeypatch, mod, make, fname):
    """Every file holding the refresh token is 0600 while the bytes are on disk."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old_umask = os.umask(0o022)
    observed: list[int] = []
    real_fdopen = os.fdopen

    class Spy:
        def __init__(self, stream):
            self._stream = stream

        def write(self, data):
            n = self._stream.write(data)
            self._stream.flush()
            observed.append(stat.S_IMODE(os.fstat(self._stream.fileno()).st_mode))
            return n

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._stream.close()
            return False

    real_write_text = Path.write_text

    def spy_write_text(self, data, *a, **k):
        n = real_write_text(self, data, *a, **k)
        observed.append(stat.S_IMODE(self.stat().st_mode))
        return n

    monkeypatch.setattr(os, "fdopen", lambda fd, *a, **k: Spy(real_fdopen(fd, *a, **k)))
    monkeypatch.setattr(Path, "write_text", spy_write_text)
    try:
        mod._save_tokens(make("new"))
    finally:
        os.umask(old_umask)

    path = _token_dir(tmp_path) / fname
    assert json.loads(path.read_text())["refresh_token"] == "refresh-new"
    assert observed, "token bytes were never written"
    assert all(mode & 0o077 == 0 for mode in observed), [oct(m) for m in observed]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(("mod", "make", "fname"), CASES)
def test_failed_write_keeps_previous_login(tmp_path, monkeypatch, mod, make, fname):
    """ENOSPC mid-save must leave the existing login intact and no temp file."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    mod._save_tokens(make("old"))
    path = _token_dir(tmp_path) / fname

    real_fdopen = os.fdopen

    class FullDisk:
        # The file is already open (and, for write_text, truncated); writing fails.
        def __init__(self, stream):
            self._stream = stream

        def write(self, _data):
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._stream.close()
            return False

    def full_disk_write_text(self, _data, *a, **k):
        with self.open("w"):  # truncates, like the real write_text
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(
        os, "fdopen", lambda fd, *a, **k: FullDisk(real_fdopen(fd, *a, **k))
    )
    monkeypatch.setattr(Path, "write_text", full_disk_write_text)
    with pytest.raises(OSError, match="No space"):
        mod._save_tokens(make("new"))
    monkeypatch.undo()

    assert json.loads(path.read_text())["refresh_token"] == "refresh-old"
    assert sorted(p.name for p in _token_dir(tmp_path).iterdir()) == [fname]
