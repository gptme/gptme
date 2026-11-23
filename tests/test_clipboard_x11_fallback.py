"""Exercise clipboard selection using executable stand-ins, without a display."""

import os
import sys
from pathlib import Path

import pytest

from gptme.util import get_installed_programs
from gptme.util.clipboard import copy, paste_text, set_copytext


@pytest.mark.skipif(os.name == "nt", reason="Executable stand-ins use POSIX shebangs")
@pytest.mark.parametrize("operation", ["copy", "paste"])
@pytest.mark.parametrize("wayland_ok", [False, True])
def test_x11_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    wayland_ok: bool,
) -> None:
    clipboard_text = "clipboard 世界"
    for name in ("wl-copy", "wl-paste", "xclip"):
        executable = tmp_path / name
        destination = tmp_path / ("x11.txt" if name == "xclip" else "wayland.txt")
        script = f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
        if name != "xclip" and not wayland_ok:
            script += "sys.exit(1)\n"
        if name == "xclip":
            script += "assert sys.argv[1:3] == ['-selection', 'clipboard']\n"
        if operation == "copy":
            script += f"Path({str(destination)!r}).write_text(sys.stdin.read())\n"
        else:
            script += f"sys.stdout.write({clipboard_text!r})\n"
            script += f"Path({str(destination)!r}).touch()\n"
        executable.write_text(script)
        executable.chmod(0o755)

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr("gptme.util.clipboard.platform.system", lambda: "Linux")
    get_installed_programs.cache_clear()
    try:
        if operation == "copy":
            set_copytext(clipboard_text)
            assert copy() is True
        else:
            assert paste_text() == clipboard_text
        chosen = tmp_path / ("wayland.txt" if wayland_ok else "x11.txt")
        assert chosen.exists()
        if operation == "copy":
            assert chosen.read_text() == clipboard_text
        if wayland_ok:
            assert not (tmp_path / "x11.txt").exists()
    finally:
        get_installed_programs.cache_clear()
