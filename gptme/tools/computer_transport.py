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
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


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

        self._is_macos = IS_MACOS
        if self._is_macos:
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
        from .computer import IS_MACOS, _macos_mouse_move, _run_xdotool

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
        else:
            import os

            display = os.getenv("DISPLAY", ":1")
            _run_xdotool("click --repeat 2 --delay 100 1", display)

    def left_click_drag(self, x: int, y: int) -> None:
        from .computer import IS_MACOS, _macos_drag, _run_xdotool

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
        from .computer import IS_MACOS, _run_xdotool

        if IS_MACOS:
            import subprocess

            output = subprocess.run(
                ["cliclick", "p"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
            x, y = map(int, output.split(","))
            return x, y
        import os

        display = os.getenv("DISPLAY", ":1")
        output = _run_xdotool("getmouselocation --shell", display)
        x = int(output.split("X=")[1].split("\n")[0])
        y = int(output.split("Y=")[1].split("\n")[0])
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
        self._sandbox: object | None = None  # typed as Any for attribute access
        self._initialized: bool = False

    def _ensure_sandbox(self) -> None:
        """Lazy-init: import cua_sandbox and create a Docker sandbox."""
        if self._initialized:
            return

        try:
            from cua_sandbox import (
                Sandbox,  # type: ignore[import-untyped,import-not-found]
            )
        except ImportError:
            raise RuntimeError(
                "cua-sandbox not installed. Install with: pip install cua-sandbox"
            ) from None

        import asyncio

        async def _create() -> object:
            sandbox = await Sandbox.create()
            return sandbox

        self._sandbox = asyncio.run(_create())
        self._initialized = True

    def _run_async(self, coro: object) -> object:
        """Run an async cua call synchronously."""
        import asyncio

        return asyncio.run(coro)  # type: ignore[arg-type]

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

    def left_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.mouse.click("left"))  # type: ignore[attr-defined, union-attr]

    def right_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.mouse.click("right"))  # type: ignore[attr-defined, union-attr]

    def middle_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.mouse.click("middle"))  # type: ignore[attr-defined, union-attr]

    def double_click(self) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.mouse.double_click())  # type: ignore[attr-defined, union-attr]

    def left_click_drag(self, x: int, y: int) -> None:
        self._ensure_sandbox()
        assert self._sandbox is not None
        self._run_async(self._sandbox.mouse.drag(x, y))  # type: ignore[attr-defined, union-attr]

    def screenshot(self, width: int = 0, height: int = 0) -> Path:
        self._ensure_sandbox()
        assert self._sandbox is not None

        import tempfile

        async def _capture() -> Path:
            ss = await self._sandbox.screen.screenshot()  # type: ignore[attr-defined, union-attr]
            path = Path(tempfile.mktemp(suffix=".png"))
            ss.save(str(path))
            if width and height:
                ss = ss.resize((width, height))
                ss.save(str(path))
            return path

        return self._run_async(_capture())  # type: ignore[return-value]

    def cursor_position(self) -> tuple[int, int]:
        self._ensure_sandbox()
        assert self._sandbox is not None

        async def _pos() -> tuple[int, int]:
            pos = await self._sandbox.mouse.get_position()  # type: ignore[attr-defined, union-attr]
            return (pos.x, pos.y)

        return self._run_async(_pos())  # type: ignore[return-value]

    def close(self) -> None:
        if self._sandbox is not None:
            self._run_async(self._sandbox.close())  # type: ignore[attr-defined, union-attr]
            self._sandbox = None
            self._initialized = False


# ---------------------------------------------------------------------------
# Transport factory
# ---------------------------------------------------------------------------

_transport: ComputerTransport | None = None
_transport_loaded: bool = False


def get_transport() -> ComputerTransport | None:
    """Return the configured transport, or None for native (default) path.

    Reads ``GPTME_COMPUTER_TRANSPORT`` env var:
    - unset / empty → None (use existing computer.py native code path)
    - ``native`` → ``NativeComputerTransport`` (explicit opt-in to
      transport-layer wrapper around xdotool/cliclick)
    - ``cua`` → ``CuaComputerTransport`` (Docker sandbox via cua-sandbox)
    """
    global _transport, _transport_loaded

    if _transport_loaded:
        return _transport

    _transport_loaded = True
    import os

    transport_name = os.getenv("GPTME_COMPUTER_TRANSPORT", "").strip()
    if not transport_name:
        return None

    if transport_name == "native":
        _transport = NativeComputerTransport()
    elif transport_name == "cua":
        try:
            _transport = CuaComputerTransport()
        except RuntimeError as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "CuaComputerTransport init failed: %s; falling back to native", e
            )
            _transport = NativeComputerTransport()
    else:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            "Unknown GPTME_COMPUTER_TRANSPORT=%r; falling back to native",
            transport_name,
        )
        _transport = NativeComputerTransport()

    return _transport
