"""Windows clipboard reads with real text buffers and a fake Win32 boundary."""

import ctypes
from unittest.mock import Mock, patch

import pytest

from gptme.util.clipboard import paste_text


@pytest.mark.parametrize("text", ["hello 世界 ✓", "café", ""])
def test_windows_paste_unicode(text: str) -> None:
    # ANSI data cannot preserve all characters. Keep a valid buffer here so the
    # old implementation fails safely rather than dereferencing a fake handle.
    ansi = ctypes.create_string_buffer(text.encode("cp1252", errors="replace"))
    unicode = ctypes.create_unicode_buffer(text)
    user32 = Mock()
    kernel32 = Mock()
    user32.GetClipboardData.return_value = ctypes.addressof(ansi)
    kernel32.GlobalLock.return_value = ctypes.addressof(unicode)
    with (
        patch("platform.system", return_value="Windows"),
        patch.object(
            ctypes, "windll", Mock(user32=user32, kernel32=kernel32), create=True
        ),
    ):
        assert paste_text() == text
    user32.GetClipboardData.assert_called_once_with(13)
    kernel32.GlobalLock.assert_called_once_with(ctypes.addressof(ansi))
    kernel32.GlobalUnlock.assert_called_once_with(ctypes.addressof(ansi))
    kernel32.GlobalFree.assert_not_called()
    assert kernel32.GlobalLock.restype is ctypes.c_void_p
    user32.CloseClipboard.assert_called_once()


@pytest.mark.parametrize("failure", ["open", "format", "data", "lock"])
def test_windows_paste_failure(failure: str) -> None:
    user32 = Mock()
    kernel32 = Mock()
    user32.OpenClipboard.return_value = failure != "open"
    user32.IsClipboardFormatAvailable.return_value = failure != "format"
    user32.GetClipboardData.return_value = 0
    kernel32.GlobalLock.return_value = 0
    buffer = ctypes.create_string_buffer(b"text")
    if failure == "lock":
        user32.GetClipboardData.return_value = ctypes.addressof(buffer)
    with (
        patch("platform.system", return_value="Windows"),
        patch.object(
            ctypes, "windll", Mock(user32=user32, kernel32=kernel32), create=True
        ),
    ):
        assert paste_text() is None
    if failure == "open":
        user32.GetClipboardData.assert_not_called()
        user32.CloseClipboard.assert_not_called()
    else:
        user32.CloseClipboard.assert_called_once()
    kernel32.GlobalUnlock.assert_not_called()
    kernel32.GlobalFree.assert_not_called()
