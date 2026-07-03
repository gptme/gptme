"""Tests for the computer-use Docker container configuration.

Verifies that the gptme-computer Docker entrypoint starts the server
with the tools required for computer-use profile functionality.

Also verifies that computer.html does not hardcode VNC hostnames so that
remote Docker setups (where gptme server and the browser are on different
machines) work without any configuration changes.
"""

from __future__ import annotations

from pathlib import Path

ENTRYPOINT = (
    Path(__file__).parent.parent / "scripts" / "computer_home" / "entrypoint.sh"
)
COMPUTER_HTML = (
    Path(__file__).parent.parent / "gptme" / "server" / "static" / "computer.html"
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


class TestComputerHtmlVncUrl:
    """Ensure computer.html derives the VNC host dynamically.

    When accessed remotely (e.g. http://remote-host:8080/computer) a hardcoded
    '127.0.0.1' would make the browser try to connect to its own localhost
    instead of the server, breaking the VNC stream.  The fix is to derive the
    host from window.location.hostname in JavaScript.
    """

    def test_computer_html_exists(self):
        assert COMPUTER_HTML.exists(), f"computer.html not found: {COMPUTER_HTML}"

    def test_vnc_src_not_hardcoded_to_localhost(self):
        """The iframe src must not contain a hardcoded '127.0.0.1' hostname.

        A hardcoded address breaks any remote Docker setup: the browser
        connects to its own loopback instead of the server's noVNC port.
        """
        html = COMPUTER_HTML.read_text()
        # The static src attribute must not contain the hardcoded address.
        # (The dynamic JS replacement is allowed to reference it as a fallback
        # label or comment, but the iframe src= itself must not.)
        assert 'src="http://127.0.0.1' not in html, (
            "computer.html iframe src is hardcoded to 127.0.0.1. "
            "Use window.location.hostname so remote setups work correctly."
        )

    def test_vnc_url_derived_from_window_location(self):
        """computer.html must use window.location.hostname to build the VNC URL."""
        html = COMPUTER_HTML.read_text()
        assert "window.location.hostname" in html, (
            "computer.html does not use window.location.hostname for the VNC URL. "
            "Remote Docker users (different machine from the server) need the host "
            "derived from the page origin, not hardcoded to 127.0.0.1."
        )

    def test_vnc_port_overridable_via_query_param(self):
        """A ?vncPort= query param must let users point at a non-default noVNC port."""
        html = COMPUTER_HTML.read_text()
        assert "vncPort" in html, (
            "computer.html does not support a vncPort query parameter override. "
            "Users running noVNC on a non-standard port need a way to override it."
        )

    def test_vnc_host_overridable_via_query_param(self):
        """A ?vncHost= query param must let users point at an explicit VNC host."""
        html = COMPUTER_HTML.read_text()
        assert "vncHost" in html, (
            "computer.html does not support a vncHost query parameter override. "
            "Users with split-horizon networking (separate VNC and gptme hosts) need this."
        )
