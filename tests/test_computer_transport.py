"""
Tests for the computer transport abstraction layer.

Covers the transport ABC contract, native transport instantiation,
and the get_transport() factory function.

Deep xdotool/subprocess integration tests live in computer.py's
existing test suite — this file validates the abstraction layer.
"""

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gptme.tools.computer_transport import (
    ComputerTransport,
    CuaComputerTransport,
    NativeComputerTransport,
    get_transport,
)


class StubTransport(ComputerTransport):
    """Minimal concrete transport for testing the ABC contract."""

    def close(self) -> None:
        pass

    def key(self, text: str) -> None:
        pass

    def type_text(self, text: str) -> None:
        pass

    def mouse_move(self, x: int, y: int) -> None:
        pass

    def left_click(self) -> None:
        pass

    def right_click(self) -> None:
        pass

    def middle_click(self) -> None:
        pass

    def double_click(self) -> None:
        pass

    def left_click_drag(self, x: int, y: int) -> None:
        pass

    def screenshot(self, width: int = 0, height: int = 0) -> Path:
        return Path("/tmp/stub.png")

    def cursor_position(self) -> tuple[int, int]:
        return (42, 17)


class TestComputerTransportABC(unittest.TestCase):
    """Abstract base class contract tests."""

    def test_concrete_subclass_instantiable(self):
        """A full concrete subclass should instantiate and work."""
        transport = StubTransport()
        self.assertIsInstance(transport, ComputerTransport)
        self.assertEqual(transport.cursor_position(), (42, 17))
        self.assertIsInstance(transport.screenshot(), Path)

    def test_incomplete_subclass_raises_typeerror(self):
        """Subclass missing abstract methods must fail at instantiation."""

        class IncompleteTransport(ComputerTransport):
            pass

        with self.assertRaises(TypeError):
            IncompleteTransport()  # type: ignore[abstract]

    def test_abstract_method_surface_matches_expected(self):
        """The 11 abstract methods match gptme's computer() action set."""
        abstract = {
            name
            for name in dir(ComputerTransport)
            if getattr(
                getattr(ComputerTransport, name, None),
                "__isabstractmethod__",
                False,
            )
        }
        expected = {
            "close",
            "key",
            "type_text",
            "mouse_move",
            "left_click",
            "right_click",
            "middle_click",
            "double_click",
            "left_click_drag",
            "screenshot",
            "cursor_position",
        }
        self.assertEqual(abstract, expected)


class TestNativeComputerTransport(unittest.TestCase):
    """Smoke tests for the native (xdotool+scrot/cliclick) transport."""

    def test_instantiation(self):
        """Instantiation does not touch subprocess."""
        transport = NativeComputerTransport()
        self.assertIsInstance(transport, ComputerTransport)

    def test_close_is_noop(self):
        """close() should be safe to call repeatedly."""
        transport = NativeComputerTransport()
        transport.close()
        transport.close()  # No exception

    def test_screenshot_signature_and_type(self):
        """screenshot() accepts optional width/height and declares Path return."""
        import inspect

        sig = inspect.signature(NativeComputerTransport.screenshot)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ["self", "width", "height"])
        # from __future__ import annotations stringifies annotations
        self.assertIn(
            sig.return_annotation,
            (Path, "Path"),
        )


class TestGetTransport(unittest.TestCase):
    """Transport factory function tests."""

    def setUp(self):
        # Reset the module-level cache between tests
        import gptme.tools.computer_transport as ct

        ct._transport = None
        ct._transport_name = None

    @patch.dict(os.environ, {}, clear=True)
    def test_default_returns_none(self):
        """Without env var, return None — use existing computer.py code path."""
        transport = get_transport()
        self.assertIsNone(transport)

    @patch.dict(os.environ, {"GPTME_COMPUTER_TRANSPORT": "native"}, clear=True)
    def test_native_env_returns_native_transport(self):
        """GPTME_COMPUTER_TRANSPORT=native selects NativeComputerTransport."""
        transport = get_transport()
        self.assertIsInstance(transport, NativeComputerTransport)

    @patch.dict(os.environ, {"GPTME_COMPUTER_TRANSPORT": "bogus"}, clear=True)
    def test_unknown_value_falls_back_gracefully(self):
        """Unknown transport name falls back to native with a warning."""
        transport = get_transport()
        self.assertIsInstance(transport, NativeComputerTransport)

    @patch.dict(os.environ, {"GPTME_COMPUTER_TRANSPORT": "cua"}, clear=True)
    def test_cua_missing_package_falls_back_to_native(self):
        """GPTME_COMPUTER_TRANSPORT=cua falls back gracefully when cua-sandbox is absent."""
        with patch.dict("sys.modules", {"cua_sandbox": None}):
            transport = get_transport()
        self.assertIsInstance(transport, NativeComputerTransport)


class TestScreenshotNotBypassed(unittest.TestCase):
    """Verify the screenshot action is routed through the transport (not local fallback)."""

    def setUp(self):
        import gptme.tools.computer_transport as ct

        ct._transport = None
        ct._transport_name = None

    def test_screenshot_dispatched_through_transport(self):
        """When a transport is active, screenshot() must be called on the transport."""
        stub = StubTransport()
        stub.screenshot = MagicMock(return_value=Path("/tmp/stub.png"))  # type: ignore[method-assign]

        with (
            patch(
                "gptme.tools.computer._dispatch_transport",
                wraps=lambda t, a, *args, **kw: (
                    t.screenshot() if a == "screenshot" else None
                ),
            ) as mock_dispatch,
            patch("gptme.tools.computer.get_transport", return_value=stub),
        ):
            from gptme.tools.computer import computer

            computer("screenshot")

        mock_dispatch.assert_called_once()
        stub.screenshot.assert_called_once()


class TestNativeTransportCoordinateScaling(unittest.TestCase):
    """Verify NativeComputerTransport applies API→physical coordinate scaling."""

    def test_mouse_move_scales_coordinates(self):
        """mouse_move() must scale from API-space to physical before calling xdotool."""
        transport = NativeComputerTransport()
        called_with: list[tuple[int, int]] = []

        def fake_xdotool(cmd: str, display: str) -> str:
            # Extract the x,y from "mousemove --sync X Y"
            parts = cmd.split()
            called_with.append((int(parts[-2]), int(parts[-1])))
            return ""

        with (
            patch("gptme.tools.computer.IS_MACOS", False),
            patch(
                "gptme.tools.computer._get_display_resolution",
                return_value=(1920, 1080),
            ),
            patch("gptme.tools.computer._run_xdotool", fake_xdotool),
            patch.dict(os.environ, {"WIDTH": "1366", "HEIGHT": "768", "DISPLAY": ":1"}),
        ):
            transport.mouse_move(683, 384)  # mid-point in API space

        self.assertEqual(len(called_with), 1)
        phys_x, phys_y = called_with[0]
        # At 1920x1080 physical vs 1366x768 API, scaling is ~1.406x and ~1.406x
        self.assertAlmostEqual(phys_x, round(683 * 1920 / 1366), delta=2)
        self.assertAlmostEqual(phys_y, round(384 * 1080 / 768), delta=2)

    def test_cursor_position_scales_to_api_space(self):
        """cursor_position() must convert physical pixels → API-space before returning."""
        transport = NativeComputerTransport()

        # Simulate xdotool reporting physical-pixel position (mid-screen at 1920x1080)
        fake_xdotool_output = "X=960\nY=540\nSCREEN=0\n"

        with (
            patch("gptme.tools.computer.IS_MACOS", False),
            patch(
                "gptme.tools.computer._get_display_resolution",
                return_value=(1920, 1080),
            ),
            patch(
                "gptme.tools.computer._run_xdotool", return_value=fake_xdotool_output
            ),
            patch.dict(os.environ, {"WIDTH": "1366", "HEIGHT": "768", "DISPLAY": ":1"}),
        ):
            api_x, api_y = transport.cursor_position()

        # Physical 960,540 on 1920x1080 → API ~683,384 on 1366x768
        self.assertAlmostEqual(api_x, round(960 * 1366 / 1920), delta=2)
        self.assertAlmostEqual(api_y, round(540 * 768 / 1080), delta=2)


class TestCuaTransportAsyncio(unittest.TestCase):
    """Verify _run_async() works both inside and outside a running event loop."""

    def test_run_async_outside_loop(self):
        """_run_async should work normally when no event loop is running."""
        transport = CuaComputerTransport.__new__(CuaComputerTransport)

        async def _coro() -> int:
            return 42

        with patch.object(CuaComputerTransport, "__init__", return_value=None):
            result = transport._run_async(_coro())
        self.assertEqual(result, 42)

    def test_run_async_inside_loop(self):
        """_run_async must not raise when called from inside a running event loop."""
        transport = CuaComputerTransport.__new__(CuaComputerTransport)

        async def _coro() -> int:
            return 99

        async def _runner() -> int:
            return transport._run_async(_coro())  # type: ignore[return-value]

        result = asyncio.run(_runner())
        self.assertEqual(result, 99)


class TestTransportCloseOnEnvVarChange(unittest.TestCase):
    """Verify get_transport() closes the old transport when the env var changes."""

    def setUp(self):
        import gptme.tools.computer_transport as ct

        ct._transport = None
        ct._transport_name = None

    def tearDown(self):
        import gptme.tools.computer_transport as ct

        ct._transport = None
        ct._transport_name = None

    def test_old_transport_closed_on_env_var_change(self):
        """Switching transport via env var must close the previous transport."""
        closed: list[bool] = []

        class RecordingTransport(StubTransport):
            def close(self) -> None:
                closed.append(True)

        import gptme.tools.computer_transport as ct

        # Pre-seed the module-level singleton as if "native" was previously active.
        ct._transport = RecordingTransport()
        ct._transport_name = "native"

        # Switching to an empty env var should close the previous transport.
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GPTME_COMPUTER_TRANSPORT", None)
            get_transport()

        self.assertEqual(
            len(closed), 1, "close() must be called once on the stale transport"
        )


class TestNativeDoubleClickCalledProcessError(unittest.TestCase):
    """macOS double_click must wrap CalledProcessError into RuntimeError."""

    def test_double_click_macos_wraps_called_process_error(self):
        """CalledProcessError from cliclick must be re-raised as RuntimeError."""
        import subprocess

        transport = NativeComputerTransport.__new__(NativeComputerTransport)

        def raise_called_process_error(*args, **kwargs):
            raise subprocess.CalledProcessError(
                1, "cliclick", stderr="permission denied"
            )

        with (
            patch("gptme.tools.computer.IS_MACOS", True),
            patch("subprocess.run", side_effect=raise_called_process_error),
            self.assertRaises(RuntimeError) as ctx,
        ):
            transport.double_click()

        self.assertIn("failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
