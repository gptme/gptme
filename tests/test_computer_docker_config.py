"""Tests for the computer-use Docker container configuration.

Verifies that the gptme-computer Docker entrypoint starts the server
with the tools required for computer-use profile functionality.
"""

from __future__ import annotations

from pathlib import Path

ENTRYPOINT = (
    Path(__file__).parent.parent / "scripts" / "computer_home" / "entrypoint.sh"
)


def _parse_server_tools(entrypoint_path: Path) -> list[str]:
    """Extract the --tools value from the entrypoint server start command."""
    text = entrypoint_path.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if "gptme.server" in stripped and "--tools" in stripped:
            # find --tools VALUE in the line
            parts = stripped.split()
            for i, part in enumerate(parts):
                if part == "--tools" and i + 1 < len(parts):
                    return parts[i + 1].split(",")
    return []


class TestDockerEntrypointTools:
    def test_entrypoint_exists(self):
        assert ENTRYPOINT.exists(), f"Entrypoint not found: {ENTRYPOINT}"

    def test_browser_tool_included(self):
        """browser tool must be in the server --tools list for structured-first web interaction.

        The computer-use profile relies on snapshot_url/open_page/fill_element/click_element
        (browser tool functions). Without browser in the tools list, those functions are
        unavailable and the agent falls back to screenshot-only — breaking the
        structured-first policy and the 'Can it Tweet?' interactive workflow.
        """
        tools = _parse_server_tools(ENTRYPOINT)
        assert tools, "Could not parse --tools from entrypoint.sh"
        assert "browser" in tools, (
            f"browser not in Docker server tools: {tools}. "
            "The computer-use profile requires browser for snapshot_url/open_page/fill_element/click_element."
        )

    def test_computer_tool_included(self):
        """computer tool must be present for native desktop/X11 interaction."""
        tools = _parse_server_tools(ENTRYPOINT)
        assert "computer" in tools, f"computer not in Docker server tools: {tools}"

    def test_vision_tool_included(self):
        """vision tool must be present for screenshot analysis."""
        tools = _parse_server_tools(ENTRYPOINT)
        assert "vision" in tools, f"vision not in Docker server tools: {tools}"

    def test_ipython_tool_included(self):
        """ipython tool must be present for code execution."""
        tools = _parse_server_tools(ENTRYPOINT)
        assert "ipython" in tools, f"ipython not in Docker server tools: {tools}"

    def test_all_computer_use_profile_tools_present(self):
        """All tools required by the computer-use profile must be in the server tools list."""
        tools = _parse_server_tools(ENTRYPOINT)
        # These match the tools= list in gptme/profiles.py for the computer-use profile
        required = {"computer", "browser", "vision", "ipython", "shell"}
        missing = required - set(tools)
        assert not missing, (
            f"Docker server missing tools required by computer-use profile: {missing}. "
            f"Current tools: {tools}"
        )
