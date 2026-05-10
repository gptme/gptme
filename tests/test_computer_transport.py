"""
Tests for the computer transport abstraction layer.

Covers the transport ABC contract, native transport instantiation,
and the get_transport() factory function.

Deep xdotool/subprocess integration tests live in computer.py's
existing test suite — this file validates the abstraction layer.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from gptme.tools.computer_transport import (
    ComputerTransport,
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


if __name__ == "__main__":
    unittest.main()
