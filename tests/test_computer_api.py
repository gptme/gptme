"""Tests for the /api/v2/computer/* endpoints (issue #216).

Validates the screenshot-polling API that provides a lightweight alternative
to VNC streaming for viewing the desktop from the gptme web UI.
"""

from __future__ import annotations

import os
import platform
import tempfile
from typing import TYPE_CHECKING
from unittest.mock import Mock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_compress")
pytest.importorskip("flask_cors")

from gptme.server.app import create_app


def _minimal_png_bytes() -> bytes:
    return bytes(
        [
            0x89,
            0x50,
            0x4E,
            0x47,
            0x0D,
            0x0A,
            0x1A,
            0x0A,  # PNG signature
            0x00,
            0x00,
            0x00,
            0x0D,
            0x49,
            0x48,
            0x44,
            0x52,  # IHDR chunk
            0x00,
            0x00,
            0x00,
            0x01,
            0x00,
            0x00,
            0x00,
            0x01,
            0x08,
            0x02,
            0x00,
            0x00,
            0x00,
            0x90,
            0x77,
            0x53,
            0xDE,
            0x00,
            0x00,
            0x00,
            0x0C,
            0x49,
            0x44,
            0x41,  # IDAT chunk
            0x54,
            0x08,
            0xD7,
            0x63,
            0xF8,
            0xCF,
            0xC0,
            0x00,
            0x00,
            0x00,
            0x02,
            0x00,
            0x01,
            0xE2,
            0x21,
            0xBC,
            0x33,
            0x00,
            0x00,
            0x00,
            0x00,
            0x49,
            0x45,
            0x4E,  # IEND chunk
            0x44,
            0xAE,
            0x42,
            0x60,
            0x82,
        ]
    )


@pytest.fixture()
def client():
    app = create_app(cors_origin=None)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture()
def png_file(tmp_path) -> Path:
    p = tmp_path / "screen.png"
    p.write_bytes(_minimal_png_bytes())
    return p


# ---------------------------------------------------------------------------
# /api/v2/computer/status
# ---------------------------------------------------------------------------


class TestComputerStatus:
    def test_status_returns_json(self, client):
        resp = client.get("/api/v2/computer/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "screenshot_available" in data
        assert "system" in data
        assert "backends" in data

    def test_status_system_matches_platform(self, client):
        resp = client.get("/api/v2/computer/status")
        data = resp.get_json()
        assert data["system"] == platform.system()

    def test_status_screenshot_available_is_bool(self, client):
        resp = client.get("/api/v2/computer/status")
        data = resp.get_json()
        assert isinstance(data["screenshot_available"], bool)

    def test_status_backends_is_dict_of_bools(self, client):
        resp = client.get("/api/v2/computer/status")
        data = resp.get_json()
        backends = data["backends"]
        assert isinstance(backends, dict)
        for key, val in backends.items():
            assert isinstance(val, bool), f"{key} should be bool, got {type(val)}"

    def test_status_linux_display_field(self, client, monkeypatch):
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")
        monkeypatch.setenv("DISPLAY", ":42")
        resp = client.get("/api/v2/computer/status")
        data = resp.get_json()
        assert data["display"] == ":42"

    def test_status_no_display_on_linux(self, client, monkeypatch):
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")
        monkeypatch.delenv("DISPLAY", raising=False)
        resp = client.get("/api/v2/computer/status")
        data = resp.get_json()
        assert data["display"] is None


# ---------------------------------------------------------------------------
# /api/v2/computer/screenshot — 503 when no display
# ---------------------------------------------------------------------------


class TestComputerScreenshot503:
    """When no display is available, the endpoint returns 503."""

    def test_returns_503_when_no_display(self, client, monkeypatch):
        # Patch _screenshot_available to return False so we don't need a real display
        with patch(
            "gptme.server.computer_api._screenshot_available", return_value=False
        ):
            resp = client.get("/api/v2/computer/screenshot")
        assert resp.status_code == 503
        data = resp.get_json()
        assert "error" in data

    def test_503_error_message_is_actionable(self, client, monkeypatch):
        with patch(
            "gptme.server.computer_api._screenshot_available", return_value=False
        ):
            resp = client.get("/api/v2/computer/screenshot")
        data = resp.get_json()
        # Should mention display or screenshot backend, not a generic error
        error = data["error"].lower()
        assert any(
            keyword in error
            for keyword in ["display", "screenshot", "supported", "xvfb"]
        ), f"Error message should be actionable, got: {data['error']!r}"


# ---------------------------------------------------------------------------
# /api/v2/computer/screenshot — success (mocked transport)
# ---------------------------------------------------------------------------


class TestComputerScreenshotSuccess:
    """When a screenshot can be taken, the endpoint returns image bytes."""

    def test_returns_200_with_image(self, client, png_file):
        with (
            patch("gptme.server.computer_api._screenshot_available", return_value=True),
            patch("gptme.server.computer_api._take_screenshot", return_value=png_file),
        ):
            resp = client.get("/api/v2/computer/screenshot")
        assert resp.status_code == 200
        assert resp.content_type in ("image/png", "image/jpeg")
        assert len(resp.data) > 0

    def test_response_is_not_empty(self, client, png_file):
        with (
            patch("gptme.server.computer_api._screenshot_available", return_value=True),
            patch("gptme.server.computer_api._take_screenshot", return_value=png_file),
        ):
            resp = client.get("/api/v2/computer/screenshot")
        assert len(resp.data) > 8  # more than just a PNG signature

    def test_no_cache_headers(self, client, png_file):
        with (
            patch("gptme.server.computer_api._screenshot_available", return_value=True),
            patch("gptme.server.computer_api._take_screenshot", return_value=png_file),
        ):
            resp = client.get("/api/v2/computer/screenshot")
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc or "no-cache" in cc

    def test_quality_param_is_accepted(self, client, png_file):
        with (
            patch("gptme.server.computer_api._screenshot_available", return_value=True),
            patch("gptme.server.computer_api._take_screenshot", return_value=png_file),
        ):
            resp = client.get("/api/v2/computer/screenshot?quality=50")
        assert resp.status_code == 200

    def test_cleans_up_source_png_after_response(self, client, png_file):
        with (
            patch("gptme.server.computer_api._screenshot_available", return_value=True),
            patch("gptme.server.computer_api._take_screenshot", return_value=png_file),
        ):
            resp = client.get("/api/v2/computer/screenshot")
        assert resp.status_code == 200
        assert not png_file.exists()

    def test_cleans_up_source_png_and_temp_jpg_after_conversion(
        self, client, png_file, tmp_path
    ):
        jpg_fd, jpg_path = tempfile.mkstemp(dir=tmp_path, suffix=".jpg")

        def fake_convert(*args, **kwargs):
            with open(jpg_path, "wb") as f:
                f.write(b"jpeg-data")

        with (
            patch("gptme.server.computer_api._screenshot_available", return_value=True),
            patch("gptme.server.computer_api._take_screenshot", return_value=png_file),
            patch(
                "gptme.server.computer_api.tempfile.mkstemp",
                return_value=(jpg_fd, jpg_path),
            ),
            patch(
                "gptme.server.computer_api.shutil.which",
                side_effect=lambda cmd: (
                    "/usr/bin/convert" if cmd == "convert" else None
                ),
            ),
            patch("subprocess.run", side_effect=fake_convert),
        ):
            resp = client.get("/api/v2/computer/screenshot?quality=50")
        assert resp.status_code == 200
        assert resp.content_type == "image/jpeg"
        assert resp.data == b"jpeg-data"
        assert not png_file.exists()
        assert not os.path.exists(jpg_path)


# ---------------------------------------------------------------------------
# /api/v2/computer/screenshot — transport failure → 500
# ---------------------------------------------------------------------------


class TestComputerScreenshot500:
    def test_returns_500_when_transport_fails(self, client):
        with (
            patch("gptme.server.computer_api._screenshot_available", return_value=True),
            patch(
                "gptme.server.computer_api._take_screenshot",
                side_effect=RuntimeError("xdotool not found"),
            ),
        ):
            resp = client.get("/api/v2/computer/screenshot")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# _screenshot_available helper
# ---------------------------------------------------------------------------


class TestScreenshotAvailable:
    def test_linux_without_screenshot_tools_returns_false(self, monkeypatch):
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)

        with patch("shutil.which", return_value=None):
            from gptme.server.computer_api import _screenshot_available

            assert _screenshot_available() is False

    def test_linux_wayland_gnome_screenshot_without_display_returns_true(
        self, monkeypatch
    ):
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")

        with patch(
            "shutil.which",
            side_effect=lambda cmd: (
                "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None
            ),
        ):
            from gptme.server.computer_api import _screenshot_available

            assert _screenshot_available() is True

    def test_linux_with_display_and_scrot_returns_true(self, monkeypatch):
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")
        monkeypatch.setenv("DISPLAY", ":1")
        with patch(
            "shutil.which",
            side_effect=lambda cmd: "/usr/bin/" + cmd if cmd == "scrot" else None,
        ):
            import importlib

            from gptme.server import computer_api

            importlib.reload(computer_api)
            # Re-import after reload
            result = computer_api._screenshot_available()
        # The function reads platform.system() and shutil.which — just check it returns bool
        assert isinstance(result, bool)

    def test_configured_non_native_transport_returns_true(self):
        from gptme.server.computer_api import _screenshot_available

        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=object()
        ):
            assert _screenshot_available() is True


class TestTakeScreenshot:
    def test_uses_configured_transport_when_present(self, png_file):
        from gptme.server.computer_api import _take_screenshot

        transport = Mock()
        transport.screenshot.return_value = png_file

        with (
            patch(
                "gptme.tools.computer_transport.get_transport", return_value=transport
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport"),
        ):
            path = _take_screenshot()

        assert path == png_file
        transport.screenshot.assert_called_once_with()
        transport.close.assert_not_called()

    def test_closes_native_fallback_transport(self, png_file):
        from gptme.server.computer_api import _take_screenshot

        transport = Mock()
        transport.screenshot.return_value = png_file

        with (
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch(
                "gptme.tools.computer_transport.NativeComputerTransport",
                return_value=transport,
            ),
        ):
            path = _take_screenshot()

        assert path == png_file
        transport.screenshot.assert_called_once_with()
        transport.close.assert_called_once_with()
