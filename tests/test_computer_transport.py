"""
Tests for the computer transport abstraction layer.

Covers the transport ABC contract, native transport instantiation,
and the get_transport() factory function.

Deep xdotool/subprocess integration tests live in computer.py's
existing test suite — this file validates the abstraction layer.
"""

import asyncio
import os
import types
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from gptme.tools.computer_transport import (
    ComputerTransport,
    CuaComputerTransport,
    NativeComputerTransport,
    _resize_image,
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

    def scroll(self, x: int, y: int, direction: str, amount: int = 3) -> None:
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
        """The 12 abstract methods match gptme's computer() action set."""
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
            "scroll",
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

    @patch.dict(os.environ, {"GPTME_COMPUTER_TRANSPORT": "cua"}, clear=True)
    def test_cua_fallback_does_not_poison_cache(self):
        """After CuaComputerTransport fallback, _transport_name must not be set to 'cua'.

        A poisoned cache would prevent retries after transient failures
        (e.g. Docker not yet running when the first call is made).
        """
        import gptme.tools.computer_transport as ct

        with patch.dict("sys.modules", {"cua_sandbox": None}):
            get_transport()

        # Cache must NOT be poisoned: _transport_name should be None (retryable)
        self.assertIsNone(ct._transport_name)


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
        transport._shutdown_loop()

    def test_run_async_inside_loop(self):
        """_run_async must not raise when called from inside a running event loop."""
        transport = CuaComputerTransport.__new__(CuaComputerTransport)

        async def _coro() -> int:
            return 99

        async def _runner() -> int:
            return transport._run_async(_coro())  # type: ignore[return-value]

        result = asyncio.run(_runner())
        self.assertEqual(result, 99)
        transport._shutdown_loop()

    def test_run_async_reuses_same_loop_between_calls(self):
        """Repeated calls must reuse one loop so sandbox clients stay valid."""
        transport = CuaComputerTransport.__new__(CuaComputerTransport)

        async def _loop_id() -> int:
            return id(asyncio.get_running_loop())

        first = transport._run_async(_loop_id())
        second = transport._run_async(_loop_id())

        self.assertEqual(first, second)
        transport._shutdown_loop()


class TestCuaTransportSandboxInit(unittest.TestCase):
    """Verify local sandbox creation uses the expected live compatibility path."""

    @staticmethod
    def _fake_cua_module(create_impl):
        class FakeCuaModule(types.ModuleType):
            Image: object
            Sandbox: object

        module = FakeCuaModule("cua_sandbox")

        class FakeImage:
            calls: list[tuple[str, str, str]] = []

            @classmethod
            def linux(
                cls, distro: str = "ubuntu", version: str = "24.04", kind: str = "vm"
            ):
                cls.calls.append((distro, version, kind))
                return {
                    "distro": distro,
                    "version": version,
                    "kind": kind,
                }

        class FakeSandbox:
            @classmethod
            async def create(cls, image, **kwargs):
                return await create_impl(image, **kwargs)

        module.Image = FakeImage
        module.Sandbox = FakeSandbox
        return module, FakeImage

    def test_ensure_sandbox_uses_local_container_image(self):
        """_ensure_sandbox() must create a local Docker-backed Linux container image."""
        captured: dict[str, object] = {}

        async def fake_create(image, **kwargs):
            captured["image"] = image
            captured["kwargs"] = kwargs
            return object()

        fake_module, fake_image = self._fake_cua_module(fake_create)

        with patch.dict("sys.modules", {"cua_sandbox": fake_module}):
            transport = CuaComputerTransport()
            transport._ensure_sandbox()
            transport._ensure_sandbox()

        self.assertTrue(transport._initialized)
        self.assertEqual(fake_image.calls, [("ubuntu", "24.04", "container")])
        self.assertEqual(
            captured["image"],
            {"distro": "ubuntu", "version": "24.04", "kind": "container"},
        )
        kwargs = captured["kwargs"]
        assert isinstance(kwargs, dict)
        self.assertTrue(kwargs["local"])
        self.assertRegex(kwargs["name"], r"^gptme-cua-[0-9a-f]{8}$")

    def test_timeout_raises_actionable_error(self):
        """Sandbox startup timeout should surface a stage-specific RuntimeError."""

        async def fake_create(image, **kwargs):
            raise TimeoutError("container not ready")

        fake_module, _ = self._fake_cua_module(fake_create)

        with patch.dict("sys.modules", {"cua_sandbox": fake_module}):
            transport = CuaComputerTransport()
            with (
                patch.object(transport, "_cleanup_local_container") as mock_cleanup,
                self.assertRaises(RuntimeError) as ctx,
            ):
                transport._ensure_sandbox()

        self.assertFalse(transport._initialized)
        self.assertIn("Timed out while starting local CUA sandbox", str(ctx.exception))
        self.assertIn("GPTME_CUA_STARTUP_TIMEOUT", str(ctx.exception))
        mock_cleanup.assert_called_once()

    def test_timeout_shuts_down_loop_on_failed_init(self):
        """Background loop must be stopped when sandbox startup times out."""

        async def fake_create(image, **kwargs):
            raise TimeoutError("container not ready")

        fake_module, _ = self._fake_cua_module(fake_create)

        with patch.dict("sys.modules", {"cua_sandbox": fake_module}):
            transport = CuaComputerTransport()
            with (
                patch.object(transport, "_cleanup_local_container"),
                patch.object(transport, "_shutdown_loop") as mock_shutdown,
                self.assertRaises(RuntimeError),
            ):
                transport._ensure_sandbox()

        mock_shutdown.assert_called_once()

    def test_exception_shuts_down_loop_on_failed_init(self):
        """Background loop must be stopped when sandbox init raises any exception."""

        async def fake_create(image, **kwargs):
            raise ValueError("docker daemon not running")

        fake_module, _ = self._fake_cua_module(fake_create)

        with patch.dict("sys.modules", {"cua_sandbox": fake_module}):
            transport = CuaComputerTransport()
            with (
                patch.object(transport, "_cleanup_local_container"),
                patch.object(transport, "_shutdown_loop") as mock_shutdown,
                self.assertRaises(RuntimeError),
            ):
                transport._ensure_sandbox()

        mock_shutdown.assert_called_once()


class TestCuaTransportLiveCompatibility(unittest.TestCase):
    """Compatibility checks against the current cua-sandbox return surface."""

    def test_screenshot_writes_byte_payload(self):
        """Top-level Sandbox.screenshot() byte payloads should be written to disk."""

        class FakeSandbox:
            async def screenshot(self):
                return b"\x89PNG\r\n\x1a\nfake"

        transport = CuaComputerTransport.__new__(CuaComputerTransport)
        transport._sandbox = FakeSandbox()
        transport._initialized = True

        path = transport.screenshot()
        try:
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"\x89PNG\r\n\x1a\nfake")
        finally:
            path.unlink(missing_ok=True)

    def test_close_prefers_destroy_for_ephemeral_sandbox(self):
        """close() should destroy ephemeral sandboxes on the current API surface."""
        calls: list[str] = []

        class FakeSandbox:
            async def destroy(self):
                calls.append("destroy")

        transport = CuaComputerTransport.__new__(CuaComputerTransport)
        transport._sandbox = FakeSandbox()
        transport._initialized = True

        transport.close()

        self.assertEqual(calls, ["destroy"])
        self.assertIsNone(transport._sandbox)
        self.assertFalse(transport._initialized)

    def test_close_shuts_down_loop_when_sandbox_is_none(self):
        """close() must stop the event loop even if _sandbox was never initialized."""
        transport = CuaComputerTransport.__new__(CuaComputerTransport)
        transport._sandbox = None
        transport._initialized = False
        transport._cursor_position = None

        with patch.object(transport, "_shutdown_loop") as mock_shutdown:
            transport.close()

        mock_shutdown.assert_called_once()

    def test_left_click_uses_tracked_cursor_position(self):
        """Clicks should reuse the last mouse_move() position on the current API."""
        calls: list[tuple[int, int, str]] = []

        class FakeMouse:
            async def click(self, x: int, y: int, button: str = "left"):
                calls.append((x, y, button))

        transport = CuaComputerTransport.__new__(CuaComputerTransport)
        transport._sandbox = types.SimpleNamespace(mouse=FakeMouse())
        transport._initialized = True
        transport._cursor_position = (12, 34)

        transport.left_click()

        self.assertEqual(calls, [(12, 34, "left")])

    def test_cursor_position_requires_tracking_when_api_has_no_query(self):
        """Without a prior move, cursor_position() should explain the current API gap."""
        transport = CuaComputerTransport.__new__(CuaComputerTransport)
        transport._sandbox = object()
        transport._initialized = True
        transport._cursor_position = None

        with self.assertRaises(RuntimeError) as ctx:
            transport.cursor_position()

        self.assertIn("Call mouse_move() first", str(ctx.exception))


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


class TestDispatchTransportClickCoordinateForwarding(unittest.TestCase):
    """_dispatch_transport must forward click coordinates via mouse_move."""

    def test_click_with_coordinate_calls_mouse_move_first(self):
        """When coordinate is provided to a click action, mouse_move is called before the click."""
        stub = StubTransport()
        stub.mouse_move = MagicMock()  # type: ignore[method-assign]
        stub.left_click = MagicMock()  # type: ignore[method-assign]

        from gptme.tools.computer import _dispatch_transport

        _dispatch_transport(stub, "left_click", coordinate=(100, 200))

        stub.mouse_move.assert_called_once_with(100, 200)
        stub.left_click.assert_called_once()

    def test_click_without_coordinate_does_not_call_mouse_move(self):
        """When no coordinate is provided, mouse_move must not be called."""
        stub = StubTransport()
        stub.mouse_move = MagicMock()  # type: ignore[method-assign]
        stub.right_click = MagicMock()  # type: ignore[method-assign]

        from gptme.tools.computer import _dispatch_transport

        _dispatch_transport(stub, "right_click")

        stub.mouse_move.assert_not_called()
        stub.right_click.assert_called_once()

    def test_all_click_actions_forward_coordinate(self):
        """All four click types forward the coordinate via mouse_move."""
        from gptme.tools.computer import _dispatch_transport

        for action in ("left_click", "right_click", "middle_click", "double_click"):
            with self.subTest(action=action):
                stub = StubTransport()
                stub.mouse_move = MagicMock()  # type: ignore[method-assign]
                setattr(stub, action, MagicMock())

                from gptme.tools.computer import Action

                _dispatch_transport(stub, cast(Action, action), coordinate=(50, 75))

                stub.mouse_move.assert_called_once_with(50, 75)
                getattr(stub, action).assert_called_once()


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


class TestNativeCursorPositionErrorHandling(unittest.TestCase):
    """NativeComputerTransport.cursor_position() must surface RuntimeError, not raw OS errors."""

    def test_macos_file_not_found_raises_runtime_error_with_install_hint(self):
        """Missing cliclick binary must produce a RuntimeError with install instructions."""
        transport = NativeComputerTransport.__new__(NativeComputerTransport)

        with (
            patch("gptme.tools.computer.IS_MACOS", True),
            patch(
                "subprocess.run", side_effect=FileNotFoundError("cliclick not found")
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            transport.cursor_position()

        self.assertIn("brew install cliclick", str(ctx.exception))

    def test_macos_malformed_output_raises_runtime_error(self):
        """Malformed cliclick output must raise RuntimeError, not ValueError."""

        transport = NativeComputerTransport.__new__(NativeComputerTransport)

        mock_result = MagicMock()
        mock_result.stdout = "not,valid,coordinates,extra"

        with (
            patch("gptme.tools.computer.IS_MACOS", True),
            patch("subprocess.run", return_value=mock_result),
            patch("gptme.tools.computer._get_api_resolution", return_value=(1366, 768)),
            patch(
                "gptme.tools.computer._get_display_resolution",
                return_value=(1920, 1080),
            ),
            self.assertRaises(RuntimeError),
        ):
            transport.cursor_position()

    def test_linux_bad_xdotool_output_raises_runtime_error(self):
        """Unexpected xdotool output must raise RuntimeError, not IndexError."""
        transport = NativeComputerTransport.__new__(NativeComputerTransport)

        with (
            patch("gptme.tools.computer.IS_MACOS", False),
            patch(
                "gptme.tools.computer._run_xdotool",
                return_value="ERROR: display not found",
            ),
            patch.dict(os.environ, {"DISPLAY": ":1"}),
            self.assertRaises(RuntimeError) as ctx,
        ):
            transport.cursor_position()

        self.assertIn("Unexpected xdotool output format", str(ctx.exception))


class TestResizeImage(unittest.TestCase):
    """Error handling for the ImageMagick resize helper."""

    def test_missing_convert_raises_actionable_runtime_error(self):
        """A missing `convert` binary must surface an install hint, not FileNotFoundError."""
        with (
            patch("subprocess.run", side_effect=FileNotFoundError()),
            self.assertRaises(RuntimeError) as ctx,
        ):
            _resize_image(Path("/tmp/shot.png"), 100, 100)
        self.assertIn("ImageMagick", str(ctx.exception))

    def test_convert_timeout_raises_runtime_error(self):
        """A convert timeout must raise RuntimeError, not leak TimeoutExpired."""
        import subprocess

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="convert", timeout=30),
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            _resize_image(Path("/tmp/shot.png"), 100, 100)
        self.assertIn("timed out", str(ctx.exception))

    def test_convert_failure_includes_stderr(self):
        """A non-zero convert exit must raise RuntimeError carrying stderr."""
        import subprocess

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    returncode=1, cmd="convert", stderr="bad image"
                ),
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            _resize_image(Path("/tmp/shot.png"), 100, 100)
        self.assertIn("bad image", str(ctx.exception))


class TestNativeTransportScroll(unittest.TestCase):
    """Scroll dispatches to the correct platform helper."""

    def test_linux_scroll_down_calls_xdotool(self):
        """scroll() on Linux must call _linux_scroll with the right args."""
        transport = NativeComputerTransport()
        calls: list[tuple] = []

        def fake_scroll(x, y, direction, display, amount=3):
            calls.append((x, y, direction, display, amount))

        with (
            patch("gptme.tools.computer.IS_MACOS", False),
            patch("gptme.tools.computer._linux_scroll", side_effect=fake_scroll),
            patch("gptme.tools.computer._get_api_resolution", return_value=(1366, 768)),
            patch(
                "gptme.tools.computer._get_display_resolution",
                return_value=(1366, 768),
            ),
            patch.dict(os.environ, {"DISPLAY": ":1"}),
        ):
            transport.scroll(100, 200, "down")

        self.assertEqual(len(calls), 1)
        _, _, direction, _, amount = calls[0]
        self.assertEqual(direction, "down")
        self.assertEqual(amount, 3)

    def test_linux_scroll_up_custom_amount(self):
        """scroll() passes the amount parameter through to _linux_scroll."""
        transport = NativeComputerTransport()
        calls: list[tuple] = []

        def fake_scroll(x, y, direction, display, amount=3):
            calls.append((x, y, direction, display, amount))

        with (
            patch("gptme.tools.computer.IS_MACOS", False),
            patch("gptme.tools.computer._linux_scroll", side_effect=fake_scroll),
            patch("gptme.tools.computer._get_api_resolution", return_value=(1366, 768)),
            patch(
                "gptme.tools.computer._get_display_resolution",
                return_value=(1366, 768),
            ),
            patch.dict(os.environ, {"DISPLAY": ":1"}),
        ):
            transport.scroll(512, 400, "up", amount=5)

        self.assertEqual(calls[0][2], "up")
        self.assertEqual(calls[0][4], 5)


class TestDispatchTransportScroll(unittest.TestCase):
    """_dispatch_transport routes scroll actions correctly."""

    def test_scroll_dispatched_with_coordinate_and_direction(self):
        """scroll with coordinate and text=direction must call transport.scroll()."""
        stub = StubTransport()
        stub.scroll = MagicMock()  # type: ignore[method-assign]

        from gptme.tools.computer import _dispatch_transport

        _dispatch_transport(stub, "scroll", text="down", coordinate=(100, 200))

        stub.scroll.assert_called_once_with(100, 200, "down")

    def test_scroll_missing_coordinate_raises(self):
        """scroll without coordinate must raise ValueError."""
        stub = StubTransport()

        from gptme.tools.computer import _dispatch_transport

        with self.assertRaises(ValueError, msg="coordinate is required for scroll"):
            _dispatch_transport(stub, "scroll", text="up")

    def test_scroll_missing_direction_raises(self):
        """scroll without text (direction) must raise ValueError."""
        stub = StubTransport()

        from gptme.tools.computer import _dispatch_transport

        with self.assertRaises(ValueError, msg="text.*direction.*required"):
            _dispatch_transport(stub, "scroll", coordinate=(100, 200))

    def test_scroll_invalid_direction_raises(self):
        """scroll with an invalid direction must raise ValueError, not silently forward."""
        stub = StubTransport()
        stub.scroll = MagicMock()  # type: ignore[method-assign]

        from gptme.tools.computer import _dispatch_transport

        with self.assertRaisesRegex(ValueError, "Invalid scroll direction"):
            _dispatch_transport(stub, "scroll", text="diagonal", coordinate=(100, 200))

        stub.scroll.assert_not_called()

    def test_scroll_direction_case_insensitive(self):
        """scroll direction must be normalised to lowercase before reaching transport."""
        stub = StubTransport()
        stub.scroll = MagicMock()  # type: ignore[method-assign]

        from gptme.tools.computer import _dispatch_transport

        _dispatch_transport(stub, "scroll", text="Down", coordinate=(100, 200))

        stub.scroll.assert_called_once_with(100, 200, "down")


if __name__ == "__main__":
    unittest.main()
