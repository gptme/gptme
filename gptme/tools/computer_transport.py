"""
Transport abstraction for computer interaction.

Provides a pluggable transport layer that allows gptme's computer tool
to dispatch through different backends:

- **Native** (xdotool+scrot or cliclick) — default, zero-dependency
- **Cua Sandbox** — opt-in via ``GPTME_COMPUTER_TRANSPORT=cua`` env var

Usage::

    from gptme.tools.computer_transport import get_transport

    transport = get_transport()
    if transport:
        transport.mouse_move(100, 200)
        transport.left_click()
        path = transport.screenshot()

Architecture follows the two-layer abstraction from trycua/cua:
Transport (command protocol) → Interface classes (typed surface).
"""

from __future__ import annotations

import abc
import asyncio
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import concurrent.futures
    from collections.abc import Coroutine


class ComputerTransport(abc.ABC):
    """ABC for the computer interaction transport layer.

    Maps 1:1 to the action surface of ``computer()`` in ``computer.py``.
    Each method corresponds to one ``Action`` literal.
    """

    @abc.abstractmethod
    def key(self, text: str) -> None:
        """Send a key sequence (e.g. 'Return', 'ctrl+c')."""
        ...

    @abc.abstractmethod
    def type_text(self, text: str) -> None:
        """Type text with realistic delays."""
        ...

    @abc.abstractmethod
    def mouse_move(self, x: int, y: int) -> None:
        """Move mouse to (x, y) in API-space coordinates."""
        ...

    @abc.abstractmethod
    def left_click(self) -> None:
        """Click left mouse button at current position."""
        ...

    @abc.abstractmethod
    def right_click(self) -> None:
        """Click right mouse button at current position."""
        ...

    @abc.abstractmethod
    def middle_click(self) -> None:
        """Click middle mouse button at current position."""
        ...

    @abc.abstractmethod
    def double_click(self) -> None:
        """Double-click left mouse button at current position."""
        ...

    @abc.abstractmethod
    def left_click_drag(self, x: int, y: int) -> None:
        """Click and drag from current position to (x, y)."""
        ...

    @abc.abstractmethod
    def screenshot(self, width: int = 0, height: int = 0) -> Path:
        """Capture and save a screenshot. Returns path to the image file."""
        ...

    @abc.abstractmethod
    def cursor_position(self) -> tuple[int, int]:
        """Return current cursor position as (x, y) in API-space."""
        ...

    @abc.abstractmethod
    def close(self) -> None:
        """Release any resources held by this transport."""
        ...


# ---------------------------------------------------------------------------
# Native transport: delegates to existing xdotool/cliclick helpers
# ---------------------------------------------------------------------------


class NativeComputerTransport(ComputerTransport):
    """Default transport: xdotool+scrot (Linux) / cliclick (macOS).

    Thin wrapper around the existing helpers in ``computer.py``.
    Provides the transport interface without changing the underlying
    subprocess dispatch.
    """

    def __init__(self) -> None:
        from .computer import IS_MACOS, _ensure_cliclick

        if IS_MACOS:
            _ensure_cliclick()

    def key(self, text: str) -> None:
        from .computer import _linux_handle_key_sequence, _macos_key, IS_MACOS  # noqa

        if IS_MACOS:
            _macos_key(text)
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            _linux_handle_key_sequence(text, display)

    def type_text(self, text: str) -> None:
        from .computer import IS_MACOS, _chunks, _linux_type, _macos_type

        if IS_MACOS:
            for chunk in _chunks(text, 50):
                _macos_type(chunk)
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            _linux_type(text, display)

    def mouse_move(self, x: int, y: int) -> None:
        from .computer import (
            IS_MACOS,
            _get_api_resolution,
            _macos_mouse_move,
            _run_xdotool,
            _scale_coordinates,
            _ScalingSource,
        )

        api_w, api_h = _get_api_resolution()
        x, y = _scale_coordinates(_ScalingSource.API, x, y, api_w, api_h)
        if IS_MACOS:
            _macos_mouse_move(x, y)
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            _run_xdotool(f"mousemove --sync {x} {y}", display)

    def left_click(self) -> None:
        self._click(1)

    def right_click(self) -> None:
        self._click(3)

    def middle_click(self) -> None:
        self._click(2)

    def _click(self, button: int) -> None:
        from .computer import IS_MACOS, _macos_click, _run_xdotool

        if IS_MACOS:
            _macos_click(button)
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            _run_xdotool(f"click {button}", display)

    def double_click(self) -> None:
        from .computer import IS_MACOS, _run_xdotool

        if IS_MACOS:
            import subprocess

            try:
                result = subprocess.run(
                    ["cliclick", "p"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                pos = result.stdout.strip()
                subprocess.run(
                    ["cliclick", f"dc:{pos}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError("cliclick double-click timed out") from e
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"cliclick double-click failed: {e.stderr}") from e
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            _run_xdotool("click --repeat 2 --delay 100 1", display)

    def left_click_drag(self, x: int, y: int) -> None:
        from .computer import (
            IS_MACOS,
            _get_api_resolution,
            _macos_drag,
            _run_xdotool,
            _scale_coordinates,
            _ScalingSource,
        )

        api_w, api_h = _get_api_resolution()
        x, y = _scale_coordinates(_ScalingSource.API, x, y, api_w, api_h)
        if IS_MACOS:
            _macos_drag(x, y)
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            _run_xdotool(f"mousedown 1 mousemove --sync {x} {y} mouseup 1", display)

    def screenshot(self, width: int = 0, height: int = 0) -> Path:
        from .screenshot import screenshot as take_screenshot

        path = take_screenshot()
        if not path.exists():
            raise RuntimeError("Screenshot failed")
        if width and height:
            import subprocess

            try:
                subprocess.run(
                    ["convert", str(path), "-resize", f"{width}x{height}!", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Image resize failed: {e.stderr}") from e
        return path

    def cursor_position(self) -> tuple[int, int]:
        from .computer import (
            IS_MACOS,
            _get_api_resolution,
            _run_xdotool,
            _scale_coordinates,
            _ScalingSource,
        )

        if IS_MACOS:
            import subprocess

            try:
                output = subprocess.run(
                    ["cliclick", "p"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout.strip()
                x, y = map(int, output.split(","))
            except subprocess.TimeoutExpired as e:
                raise RuntimeError("cliclick cursor position query timed out") from e
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to get cursor position: {e.stderr}") from e
            except FileNotFoundError as e:
                raise RuntimeError(
                    "cliclick not found. Install with: brew install cliclick"
                ) from e
            except ValueError as e:
                raise RuntimeError(
                    f"Unexpected cliclick output format: {output!r}"
                ) from e
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            output = _run_xdotool("getmouselocation --shell", display)
            if "X=" not in output or "Y=" not in output:
                raise RuntimeError(f"Unexpected xdotool output format: {output!r}")
            x = int(output.split("X=")[1].split("\n")[0])
            y = int(output.split("Y=")[1].split("\n")[0])

        api_w, api_h = _get_api_resolution()
        x, y = _scale_coordinates(_ScalingSource.COMPUTER, x, y, api_w, api_h)
        return x, y

    def close(self) -> None:
        pass  # No resources to release for native transport


# ---------------------------------------------------------------------------
# Cua sandbox transport: delegates to trycua/cua Sandbox
# ---------------------------------------------------------------------------


class CuaComputerTransport(ComputerTransport):
    """Transport backed by a trycua/cua Docker sandbox.

    Opt-in via ``GPTME_COMPUTER_TRANSPORT=cua``. Requires the
    ``cua-sandbox`` Python package installed in the environment.

    The sandbox is created on first use (lazy init) and lives for the
    lifetime of the transport. All calls are synchronous wrappers
    around cua's async interfaces.
    """

    def __init__(self) -> None:
        # Probe import eagerly so get_transport()'s try/except catches missing deps.
        try:
            import cua_sandbox as _  # type: ignore[import-untyped,import-not-found] # noqa: F401
        except ImportError:
            raise RuntimeError(
                "cua-sandbox not installed. Install with: pip install cua-sandbox"
            ) from None
        self._sandbox: object | None = None  # typed as Any for attribute access
        self._initialized: bool = False
        self._startup_timeout = self._read_startup_timeout()
        self._cursor_position: tuple[int, int] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    @staticmethod
    def _read_startup_timeout() -> float:
        import os

        raw = os.getenv("GPTME_CUA_STARTUP_TIMEOUT", "20").strip()
        try:
            timeout = float(raw)
        except ValueError:
            raise RuntimeError(
                f"GPTME_CUA_STARTUP_TIMEOUT must be numeric, got {raw!r}"
            ) from None
        if timeout <= 0:
            raise RuntimeError(f"GPTME_CUA_STARTUP_TIMEOUT must be > 0, got {raw!r}")
        return timeout

    @staticmethod
    def _cleanup_local_container(name: str) -> None:
        import shutil
        import subprocess

        docker = shutil.which("docker")
        if not docker:
            return
        try:
            subprocess.run(
                [docker, "rm", "-f", name],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            pass

    def _ensure_sandbox(self) -> None:
        """Lazy-init: create the Docker sandbox on first use."""
        if self._initialized:
            return

        import uuid

        from cua_sandbox import (
            Image,  # type: ignore[import-untyped,import-not-found]
            Sandbox,  # type: ignore[import-untyped,import-not-found]
        )

        image = Image.linux(kind="container")
        name = f"gptme-cua-{uuid.uuid4().hex[:8]}"

        async def _create() -> object:
            import asyncio

            sandbox = await asyncio.wait_for(
                Sandbox.create(image, local=True, name=name),
                timeout=self._startup_timeout,
            )
            return sandbox

        try:
            self._sandbox = self._run_async(_create())
        except TimeoutError as e:
            self._cleanup_local_container(name)
            raise RuntimeError(
                "Timed out while starting local CUA sandbox "
                f"(image=linux/ubuntu:24.04 kind=container, timeout={self._startup_timeout:g}s). "
                "Docker may still be pulling the image or the computer-server "
                "did not become ready. Retry after warming the image, or raise "
                "GPTME_CUA_STARTUP_TIMEOUT for cold starts."
            ) from e
        except Exception as e:
            self._cleanup_local_container(name)
            raise RuntimeError(
                "Failed to start local CUA sandbox "
                f"(image=linux/ubuntu:24.04 kind=container): {e}"
            ) from e
        self._initialized = True

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        loop = getattr(self, "_loop", None)
        thread = getattr(self, "_loop_thread", None)
        if loop is not None and thread is not None and thread.is_alive():
            return loop

        ready = threading.Event()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()

        thread = threading.Thread(
            target=_runner,
            name="gptme-cua-transport-loop",
            daemon=True,
        )
        thread.start()
        ready.wait()
        self._loop_thread = thread
        assert self._loop is not None
        return self._loop

    def _shutdown_loop(self) -> None:
        loop = getattr(self, "_loop", None)
        thread = getattr(self, "_loop_thread", None)
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        self._loop = None
        self._loop_thread = None

    def _run_async(self, coro: Coroutine[Any, Any, object]) -> object:
        """Run all async CUA work on one dedicated loop.

        The live cua-sandbox transport keeps async clients and connections on
        the loop that created them, so creating a fresh loop per method call
        breaks subsequent operations. A single background loop keeps the
        sandbox session stable across screenshot/mouse/keyboard calls.
        """
        loop = self._ensure_loop()
        future: concurrent.futures.Future[object] = asyncio.run_coroutine_threadsafe(
            coro, loop
        )
        return future.result()

    def key(self, text: str) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.keyboard.key(text))  # type: ignore[attr-defined, union-attr]

    def type_text(self, text: str) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.keyboard.type(text))  # type: ignore[attr-defined, union-attr]

    def mouse_move(self, x: int, y: int) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.mouse.move(x, y))  # type: ignore[attr-defined, union-attr]
        self._cursor_position = (x, y)

    def _require_cursor_position(self) -> tuple[int, int]:
        if self._cursor_position is None:
            raise RuntimeError(
                "CUA transport cannot query the live cursor position from the "
                "installed cua-sandbox API. Call mouse_move() first so the "
                "transport can track the cursor locally."
            )
        return self._cursor_position

    def left_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        x, y = self._require_cursor_position()
        self._run_async(self._sandbox.mouse.click(x, y, "left"))  # type: ignore[attr-defined, union-attr]

    def right_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        x, y = self._require_cursor_position()
        self._run_async(self._sandbox.mouse.right_click(x, y))  # type: ignore[attr-defined, union-attr]

    def middle_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        x, y = self._require_cursor_position()
        self._run_async(self._sandbox.mouse.click(x, y, "middle"))  # type: ignore[attr-defined, union-attr]

    def double_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        x, y = self._require_cursor_position()
        self._run_async(self._sandbox.mouse.double_click(x, y))  # type: ignore[attr-defined, union-attr]

    def left_click_drag(self, x: int, y: int) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        start_x, start_y = self._require_cursor_position()
        self._run_async(self._sandbox.mouse.drag(start_x, start_y, x, y))  # type: ignore[attr-defined, union-attr]
        self._cursor_position = (x, y)

    def screenshot(self, width: int = 0, height: int = 0) -> Path:
        self._ensure_sandbox()
        assert self._sandbox is not None

        import subprocess
        import tempfile

        async def _capture() -> Path:
            fd, tmp = tempfile.mkstemp(suffix=".png")
            import os as _os

            _os.close(fd)
            path = Path(tmp)
            if hasattr(self._sandbox, "screenshot"):
                screenshot = await self._sandbox.screenshot()  # type: ignore[attr-defined, union-attr]
            else:
                screenshot = await self._sandbox.screen.screenshot()  # type: ignore[attr-defined, union-attr]

            if isinstance(screenshot, bytes):
                path.write_bytes(screenshot)
            else:
                screenshot.save(str(path))
            if width and height:
                subprocess.run(
                    ["convert", str(path), "-resize", f"{width}x{height}!", str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            return path

        return self._run_async(_capture())  # type: ignore[return-value]

    def cursor_position(self) -> tuple[int, int]:
        self._ensure_sandbox()
        return self._require_cursor_position()

    def close(self) -> None:
        if self._sandbox is not None:
            try:
                if hasattr(self._sandbox, "destroy"):
                    self._run_async(self._sandbox.destroy())  # type: ignore[attr-defined, union-attr]
                elif hasattr(self._sandbox, "close"):
                    self._run_async(self._sandbox.close())  # type: ignore[attr-defined, union-attr]
                elif hasattr(self._sandbox, "disconnect"):
                    self._run_async(self._sandbox.disconnect())  # type: ignore[attr-defined, union-attr]
            finally:
                self._sandbox = None
                self._initialized = False
                self._cursor_position = None
                self._shutdown_loop()


# ---------------------------------------------------------------------------
# Transport factory
# ---------------------------------------------------------------------------

_transport: ComputerTransport | None = None
_transport_name: str | None = None  # env-var value the cache was built for


def get_transport() -> ComputerTransport | None:
    """Return the configured transport, or None for native (default) path.

    Reads ``GPTME_COMPUTER_TRANSPORT`` env var:
    - unset / empty → None (use existing computer.py native code path)
    - ``native`` → ``NativeComputerTransport`` (explicit opt-in to
      transport-layer wrapper around xdotool/cliclick)
    - ``cua`` → ``CuaComputerTransport`` (Docker sandbox via cua-sandbox)

    Caches the transport object.  Re-validates against the current env
    var on every call so that tests can switch backends without process
    restarts.
    """
    global _transport, _transport_name

    import os

    current = os.getenv("GPTME_COMPUTER_TRANSPORT", "").strip()

    # Cache hit — env var hasn't changed since last creation
    if _transport is not None and _transport_name == current:
        return _transport

    # Env var changed — close the stale transport to avoid resource leaks
    if _transport is not None and _transport_name != current:
        _transport.close()
        _transport = None

    # No transport requested
    if not current:
        _transport_name = None
        return None

    if current == "native":
        _transport = NativeComputerTransport()
        _transport_name = current
    elif current == "cua":
        try:
            _transport = CuaComputerTransport()
            _transport_name = current  # only cache on success
        except RuntimeError as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "CuaComputerTransport init failed: %s; falling back to native", e
            )
            _transport = NativeComputerTransport()
            # Leave _transport_name as None so the next call retries CuaComputerTransport
            # (allows recovery from transient failures like Docker not yet running)
            _transport_name = None
    else:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            "Unknown GPTME_COMPUTER_TRANSPORT=%r; falling back to native",
            current,
        )
        _transport = NativeComputerTransport()
        _transport_name = current

    return _transport
