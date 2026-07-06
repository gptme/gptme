"""
Computer-use API endpoints for the gptme server.

Provides a lightweight screenshot-polling alternative to VNC streaming,
allowing the web UI to show the current desktop state without requiring
a separate x11vnc + noVNC stack.

Endpoints
---------
``GET /api/v2/computer/screenshot``
    Take a screenshot and return it as a JPEG image.  Returns 503 when
    no display is available (``$DISPLAY`` not set on Linux, or
    ``screencapture`` absent on macOS).

``GET /api/v2/computer/status``
    Return a JSON status object describing what computer-use backends
    are available on this server.

Designed for issue #216 — the non-Docker local computer-use workflow
where you run ``gptme-server`` on a machine that already has a desktop,
but don't want to set up a full VNC stack just to observe the desktop.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import tempfile
from pathlib import Path

import flask

from .auth import require_auth

logger = logging.getLogger(__name__)

computer_api = flask.Blueprint("computer_api", __name__)


def _native_screenshot_available() -> bool:
    """Return True when the native screenshot backend is available.

    Mirrors the logic in ``gptme.tools.screenshot._is_available`` to ensure
    the API endpoint's availability check matches the actual screenshot tool
    backends.
    """
    system = platform.system()
    if system == "Linux":
        display = os.environ.get("DISPLAY", "")
        if not display:
            return False
        has_gnome = bool(shutil.which("gnome-screenshot"))
        is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"
        has_scrot = bool(shutil.which("scrot")) and not is_wayland
        return has_gnome or has_scrot
    if system == "Darwin":
        return bool(shutil.which("screencapture"))
    return False


def _screenshot_available() -> bool:
    """Return True when a screenshot transport is available on this machine."""
    from ..tools.computer_transport import NativeComputerTransport, get_transport

    transport = get_transport()
    if transport is None:
        return _native_screenshot_available()

    if isinstance(transport, NativeComputerTransport):
        return _native_screenshot_available()

    return True


def _take_screenshot() -> Path:
    """Take a screenshot and return the path to the saved image file.

    Raises RuntimeError when the screenshot fails.
    """
    from ..tools.computer_transport import NativeComputerTransport, get_transport

    transport = get_transport()
    owns_transport = False
    if transport is None:
        transport = NativeComputerTransport()
        owns_transport = True
    try:
        path = transport.screenshot()
        if not path.exists():
            raise RuntimeError("Screenshot produced no file")
        return path
    finally:
        if owns_transport:
            transport.close()


@computer_api.route("/api/v2/computer/screenshot")
@require_auth
def screenshot():
    """Take a screenshot of the current desktop and return it as a JPEG image.

    Returns 503 when no display backend is available.  Returns 500 on
    screenshot failure.  On success, returns the JPEG image with
    ``Content-Type: image/jpeg``.

    Query parameters
    ----------------
    quality : int, optional
        JPEG quality (1–95, default 80).  Lower values produce smaller
        images with faster polling.
    """
    if not _screenshot_available():
        system = platform.system()
        if system == "Linux":
            hint = (
                "No X11 display available. "
                "Set $DISPLAY or start Xvfb: Xvfb :1 -screen 0 1024x768x24 &"
            )
        elif system == "Darwin":
            hint = "screencapture not found (unexpected on macOS)"
        else:
            hint = f"Computer-use not supported on {system}"
        return flask.Response(
            response=flask.json.dumps({"error": hint}),
            status=503,
            content_type="application/json",
        )

    try:
        quality_raw = flask.request.args.get("quality", "80")
        try:
            quality = max(1, min(95, int(quality_raw)))
        except ValueError:
            quality = 80

        path = _take_screenshot()
        temp_paths = [path]

        try:
            # Convert to JPEG with requested quality using ImageMagick if available,
            # otherwise serve the raw PNG.
            if path.suffix.lower() != ".jpg" and shutil.which("convert"):
                try:
                    import subprocess

                    jpg_fd, jpg_path = tempfile.mkstemp(suffix=".jpg")
                    os.close(jpg_fd)
                    img_path = Path(jpg_path)
                    temp_paths.append(img_path)
                    subprocess.run(
                        [
                            "convert",
                            str(path),
                            "-quality",
                            str(quality),
                            jpg_path,
                        ],
                        check=True,
                        capture_output=True,
                        timeout=10,
                    )
                    content_type = "image/jpeg"
                except Exception:
                    img_path = path
                    content_type = "image/png"
            else:
                img_path = path
                content_type = (
                    "image/jpeg" if path.suffix.lower() == ".jpg" else "image/png"
                )

            data = img_path.read_bytes()
        finally:
            for temp_path in temp_paths:
                temp_path.unlink(missing_ok=True)

        return flask.Response(
            response=data,
            status=200,
            content_type=content_type,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )
    except Exception as exc:
        logger.exception("Screenshot failed")
        return flask.Response(
            response=flask.json.dumps({"error": str(exc)}),
            status=500,
            content_type="application/json",
        )


@computer_api.route("/api/v2/computer/status")
@require_auth
def status():
    """Return a JSON object describing what computer-use backends are available.

    Response fields
    ---------------
    screenshot_available : bool
        True when taking a screenshot will succeed.
    system : str
        Platform name (Linux / Darwin / Windows).
    display : str | null
        Value of ``$DISPLAY`` on Linux, null elsewhere.
    backends : dict
        Availability of each backend tool (xdotool, scrot, cliclick, etc.).
    """
    system = platform.system()
    display = os.environ.get("DISPLAY") if system == "Linux" else None

    backends: dict[str, bool] = {}
    if system == "Linux":
        backends["xdotool"] = bool(shutil.which("xdotool"))
        backends["scrot"] = bool(shutil.which("scrot"))
        backends["gnome_screenshot"] = bool(shutil.which("gnome-screenshot"))
        backends["imagemagick"] = bool(shutil.which("convert"))
    elif system == "Darwin":
        backends["screencapture"] = bool(shutil.which("screencapture"))
        backends["cliclick"] = bool(shutil.which("cliclick"))
        backends["osascript"] = bool(shutil.which("osascript"))

    try:
        import pyatspi  # type: ignore[import-not-found]  # noqa: F401

        backends["pyatspi"] = True
    except ImportError:
        if system == "Linux":
            backends["pyatspi"] = False

    try:
        from playwright.sync_api import (
            sync_playwright,  # type: ignore[import-not-found]  # noqa: F401
        )

        backends["playwright"] = True
    except ImportError:
        backends["playwright"] = False

    return flask.Response(
        response=flask.json.dumps(
            {
                "screenshot_available": _screenshot_available(),
                "system": system,
                "display": display,
                "backends": backends,
            }
        ),
        status=200,
        content_type="application/json",
    )
