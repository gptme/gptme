"""Native X11 integration tests for the computer tool (issue #216).

Validates the full 'screenshot → window_focus → type → wait_for_change'
pipeline using real xdotool/scrot against a headless Xvfb display.

These tests are the counterpart to ``test_computer_use_integration.py``
(which covers the browser/Playwright path).  Together they prove both legs
of the computer-use stack work end-to-end.

Run manually (requires xdotool, scrot, Xvfb, xterm, fluxbox):
    pytest tests/test_computer_x11_integration.py -v -m x11

Marked ``x11`` and automatically skipped when the required tools are absent,
so they never block CI in environments without X11.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------


_REQUIRED_X11_TOOLS = ("Xvfb", "xdotool", "scrot", "xterm", "fluxbox")
_MISSING_X11_TOOLS = [c for c in _REQUIRED_X11_TOOLS if not shutil.which(c)]

pytestmark = pytest.mark.skipif(
    bool(_MISSING_X11_TOOLS),
    reason=f"x11 tools missing: {', '.join(_MISSING_X11_TOOLS)}",
)


def _cmd_ok(*cmds: str) -> bool:
    """Return True if all commands are on PATH."""
    return all(shutil.which(c) for c in cmds)


def _pil_available() -> bool:
    return importlib.util.find_spec("PIL") is not None


@pytest.fixture(autouse=True, scope="module")
def _x11_tools_or_skip():
    """Skip the whole module if required tools are missing."""
    if _MISSING_X11_TOOLS:
        pytest.skip(f"x11 tools missing: {', '.join(_MISSING_X11_TOOLS)}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def xvfb_display() -> Generator[str, None, None]:
    """Start a private Xvfb display and yield the DISPLAY string.

    Uses display :98 to avoid collisions with production displays (:1, :0).
    The display is torn down after the module finishes.
    """
    display = ":98"
    proc = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1024x768x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give Xvfb a moment to start accepting connections
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["xdotool", "getmouselocation"],
            env={**os.environ, "DISPLAY": display},
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            break
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.skip("Xvfb did not start within 10s")

    # Start a minimal window manager so window_focus / windowactivate work.
    # Wait up to 3s for it to accept EWMH requests (CI can be slow).
    wm_proc = subprocess.Popen(
        ["fluxbox"],
        env={**os.environ, "DISPLAY": display},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wm_deadline = time.monotonic() + 3
    while time.monotonic() < wm_deadline:
        wm_check = subprocess.run(
            ["xdotool", "set_desktop", "0"],
            env={**os.environ, "DISPLAY": display},
            capture_output=True,
            check=False,
        )
        if wm_check.returncode == 0:
            break
        if wm_proc.poll() is not None:
            # fluxbox exited prematurely — skip rather than fail
            proc.kill()
            pytest.skip(
                f"fluxbox exited with {wm_proc.returncode} before tests could run"
            )
        time.sleep(0.1)
    # Allow timeout as long as fluxbox stayed alive; xterm mapping will verify activation.

    yield display

    wm_proc.terminate()
    proc.terminate()
    # fluxbox can take longer than 5s to respond to SIGTERM; SIGKILL as fallback
    try:
        wm_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        wm_proc.kill()
        wm_proc.wait(timeout=2)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


@pytest.fixture(autouse=True)
def _set_display(xvfb_display, monkeypatch):
    """Point the computer tool at the private Xvfb display for every test."""
    monkeypatch.setenv("DISPLAY", xvfb_display)
    # Clear any transport override so we use the native xdotool path
    monkeypatch.delenv("GPTME_COMPUTER_TRANSPORT", raising=False)


@pytest.fixture()
def xterm_window(xvfb_display) -> Generator[subprocess.Popen, None, None]:
    """Launch an xterm on the test display and yield the process.

    The terminal is killed after each test so tests start with a clean
    display.
    """
    env = {**os.environ, "DISPLAY": xvfb_display}
    # Use bash --norc so the PS1 doesn't override the -T title via escape codes;
    # this keeps WM_NAME stable and lets _linux_window_focus (--name search) find it.
    proc = subprocess.Popen(
        [
            "xterm",
            "-T",
            "GptmeTestXterm",
            "-geometry",
            "80x24+100+100",
            "-e",
            "bash",
            "--norc",
            "--noprofile",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for the window to appear — xdotool search --sync blocks until found;
    # enforce an outer timeout via subprocess.run(timeout=...) since this version
    # of xdotool does not support a --timeout flag.
    try:
        result = subprocess.run(
            ["xdotool", "search", "--sync", "--limit", "1", "--name", "GptmeTestXterm"],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.skip("xterm window did not appear within 10s")
    if result.returncode != 0:
        proc.kill()
        pytest.skip(
            f"xdotool search failed (rc={result.returncode}): {result.stderr!r}"
        )
    yield proc
    proc.kill()
    proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _pixel_stddev(path: Path) -> float:
    """Return the pixel standard deviation of an image (0 = blank/uniform)."""
    import statistics
    import struct as _struct

    from PIL import Image

    img = Image.open(path).convert("L")  # grayscale
    pixels = list(_struct.unpack(f"{img.width * img.height}B", img.tobytes()))
    if len(pixels) < 2:
        return 0.0
    mean = sum(pixels) / len(pixels)
    return statistics.mean((p - mean) ** 2 for p in pixels) ** 0.5


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.x11
@pytest.mark.integration
def test_screenshot_returns_image(tmp_path, monkeypatch):
    """computer('screenshot') returns a Message containing a real PNG file."""
    monkeypatch.setenv("WIDTH", "1024")
    monkeypatch.setenv("HEIGHT", "768")

    from gptme.tools.computer import computer

    msg = computer("screenshot")

    assert msg is not None, "computer('screenshot') returned None"
    assert msg.files, "screenshot message has no attached files"
    shot = msg.files[0]
    assert isinstance(shot, Path), f"expected Path, got {type(shot)}"
    assert shot.exists(), f"screenshot file does not exist: {shot}"
    assert shot.suffix.lower() == ".png"
    assert shot.stat().st_size > 0, "screenshot file is empty"


@pytest.mark.x11
@pytest.mark.integration
def test_screenshot_is_not_uniform(tmp_path, xterm_window, monkeypatch):
    """A screenshot with an xterm visible should have non-uniform pixels.

    A completely black or white image would suggest scrot captured nothing.
    """
    if not _pil_available():
        pytest.skip("PIL not installed")

    monkeypatch.setenv("WIDTH", "1024")
    monkeypatch.setenv("HEIGHT", "768")

    from gptme.tools.computer import computer

    msg = computer("screenshot")
    assert msg is not None
    shot = msg.files[0]
    assert isinstance(shot, Path)

    stddev = _pixel_stddev(shot)
    assert stddev > 5.0, (
        f"Screenshot appears uniform (std-dev={stddev:.1f}) — "
        "scrot may not be capturing xterm content"
    )


@pytest.mark.x11
@pytest.mark.integration
def test_window_focus_targets_xterm(xterm_window, monkeypatch):
    """computer('window_focus', text='GptmeTestXterm') does not raise."""
    from gptme.tools.computer import computer

    # Should not raise RuntimeError("No window matching ...")
    result = computer("window_focus", text="GptmeTestXterm")
    # window_focus returns None on success
    assert result is None


@pytest.mark.x11
@pytest.mark.integration
def test_type_changes_screen(xterm_window, monkeypatch):
    """computer('type', text=...) after window_focus updates the terminal display.

    This validates the full 'focus → type → observe' loop that the
    "Can it Tweet?" milestone relies on for web form interaction.
    """
    monkeypatch.setenv("WIDTH", "1024")
    monkeypatch.setenv("HEIGHT", "768")

    from gptme.tools.computer import computer

    # Focus the xterm so keystrokes land there
    computer("window_focus", text="GptmeTestXterm")
    time.sleep(0.3)

    # Baseline screenshot
    before_msg = computer("screenshot")
    assert before_msg is not None

    # Type something visible (newline forces shell prompt to redraw)
    computer("type", text="echo gptme_native_test\n")

    # Wait for the terminal to render (up to 5 seconds).
    # wait_for_change takes a fresh baseline after typing and polls until the
    # screen changes.  If the xterm already rendered before the baseline is
    # captured, wait_for_change sees no delta and times out, returning None -
    # in which case the fallback screenshot() below captures the settled state.
    after_msg = computer("wait_for_change", text="5")
    if after_msg is None:
        after_msg = computer("screenshot")
    assert after_msg is not None

    if not _pil_available():
        pytest.skip("PIL not installed — cannot compare screenshots")

    before_path = before_msg.files[0]
    after_path = after_msg.files[0]
    assert isinstance(before_path, Path) and isinstance(after_path, Path)

    # At least some pixels must have changed relative to the pre-type baseline.
    # Cursor blink alone gives ~0.001; rendered text gives >>0.001.
    from gptme.tools.computer import _compute_change_ratio

    ratio = _compute_change_ratio(before_path, after_path)
    assert ratio > 0.001, (
        f"Screen did not change after typing (changed ratio={ratio:.4f}) — "
        "xdotool type may not be delivering keystrokes to the xterm"
    )


@pytest.mark.x11
@pytest.mark.integration
def test_wait_for_change_detects_terminal_output(xterm_window, monkeypatch):
    """wait_for_change returns as soon as the xterm renders new output.

    This is the context-efficient polling primitive used by act_and_observe().
    It should return well before its 10-second timeout when xterm renders text.
    """
    monkeypatch.setenv("WIDTH", "1024")
    monkeypatch.setenv("HEIGHT", "768")

    from gptme.tools.computer import computer

    computer("window_focus", text="GptmeTestXterm")
    time.sleep(0.3)

    # Kick off wait_for_change before we type so it races the terminal render
    import threading

    result_holder: list = []

    def _wait():
        msg = computer("wait_for_change", text="8")
        result_holder.append(msg)

    # Warm up the screenshot pipeline so the first snapshot inside the
    # wait_for_change thread lands quickly, then start polling.
    computer("screenshot")

    t = threading.Thread(target=_wait, daemon=True)
    t.start()

    # Allow the thread to start and take its baseline screenshot before we type.
    # The warm-up screenshot above makes this generous window reliable; a true
    # synchronization primitive would require modifying the production
    # wait_for_change handler to accept an Event, which is disproportionate for
    # a manual-only (-m x11) test.
    time.sleep(1.0)

    # Type a command — this should trigger a screen change
    computer("type", text="echo wait_for_change_test\n")

    t.join(timeout=12)
    assert not t.is_alive(), "wait_for_change thread did not return within 12s"
    assert result_holder, "wait_for_change returned no result"
    msg = result_holder[0]
    assert msg is not None, "wait_for_change returned None instead of a screenshot"


@pytest.mark.x11
@pytest.mark.integration
def test_act_and_observe_full_loop(xterm_window, monkeypatch):
    """act_and_observe() dispatches an action and returns an automatic screenshot.

    This is the top-level primitive the computer-use profile recommends for
    all interactive steps.  It should return a list with at least one Message
    containing a screenshot.
    """
    monkeypatch.setenv("WIDTH", "1024")
    monkeypatch.setenv("HEIGHT", "768")

    from gptme.tools.computer import act_and_observe, computer

    computer("window_focus", text="GptmeTestXterm")
    time.sleep(0.3)

    msgs = act_and_observe("type", text="echo act_and_observe_ok\n", timeout=5.0)

    assert msgs, "act_and_observe() returned empty list"
    # At least one message should carry a screenshot
    images = [m for m in msgs if getattr(m, "files", None)]
    assert images, "act_and_observe() returned no messages with screenshot files"
    shot = images[0].files[0]
    assert isinstance(shot, Path), f"expected Path, got {type(shot)}"
    assert shot.exists(), f"Screenshot file missing: {shot}"
    assert shot.stat().st_size > 0
