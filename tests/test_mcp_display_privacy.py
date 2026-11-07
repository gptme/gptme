"""Conversational MCP diagnostics reuse the CLI's credential-safe URL display."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gptme.config import MCPServerConfig
from gptme.tools import mcp, mcp_adapter


@pytest.mark.parametrize("module", ["gptme.tools.mcp_adapter", "gptme.tools.mcp"])
def test_import_management_tools_does_not_load_mcp_sdk(module: str) -> None:
    """Tool discovery must not load the optional SDK before MCP is used."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {module}; import sys; "
            "assert 'gptme.mcp.client' not in sys.modules; "
            "assert 'mcp' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", ["list", "info fixture"])
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://alice:SYNTHETIC_PASSWORD@example.invalid/mcp"
            "?unusual=SYNTHETIC_QUERY#token=SYNTHETIC_FRAGMENT",
            "https://example.invalid/mcp?unusual=***#token=***",
        ),
        ("https://example.invalid/mcp", "https://example.invalid/mcp"),
        (
            "https://alice:SYNTHETIC_PASSWORD@[2001:db8::1]:8443/mcp",
            "https://[2001:db8::1]:8443/mcp",
        ),
        (
            "https://alice:SYNTHETIC_PASSWORD@example.invalid:abc/mcp",
            "https://example.invalid:abc/mcp",
        ),
        ("https://[broken?token=SYNTHETIC_QUERY", "<unparseable URL>"),
    ],
)
def test_conversational_url_display(command: str, url: str, expected: str) -> None:
    server = MCPServerConfig(
        name="fixture",
        enabled=False,
        url=url,
        headers={"Authorization": "Bearer SYNTHETIC_HEADER"},
    )
    config = SimpleNamespace(mcp=SimpleNamespace(servers=[server]))
    with (
        patch("gptme.config.get_config", return_value=config),
        patch.object(mcp_adapter, "get_config", return_value=config),
    ):
        output = "\n".join(
            message.content for message in mcp.execute_mcp(command, None, None)
        )
    assert expected in output
    assert "fixture" in output
    for secret in (
        "SYNTHETIC_PASSWORD",
        "SYNTHETIC_QUERY",
        "SYNTHETIC_FRAGMENT",
        "SYNTHETIC_HEADER",
    ):
        assert secret not in output
    assert server.url == url
    assert server.headers == {"Authorization": "Bearer SYNTHETIC_HEADER"}
    if command.startswith("info"):
        assert "**Headers:** 1 configured" in output


@pytest.mark.parametrize("command", ["list", "info fixture"])
def test_conversational_stdio_display_unchanged(command: str) -> None:
    server = MCPServerConfig(
        name="fixture", enabled=False, command="python", args=["-m", "fixture"]
    )
    config = SimpleNamespace(mcp=SimpleNamespace(servers=[server]))
    with (
        patch("gptme.config.get_config", return_value=config),
        patch.object(mcp_adapter, "get_config", return_value=config),
    ):
        output = "\n".join(
            message.content for message in mcp.execute_mcp(command, None, None)
        )
    assert "python" in output
    assert "URL:" not in output
    if command.startswith("info"):
        assert "-m, fixture" in output
