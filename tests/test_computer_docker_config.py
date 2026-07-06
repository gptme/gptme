"""Tests for the computer-use Docker container configuration.

Verifies that the gptme-computer Docker entrypoint starts the server
with the tools required for computer-use profile functionality.

Also verifies that computer.html does not hardcode VNC hostnames so that
remote Docker setups (where gptme server and the browser are on different
machines) work without any configuration changes.

Also verifies that docker-compose.yml includes a computer-use service that
wires up the Dockerfile.computer image with the correct ports, environment,
and profiles configuration.
"""

from __future__ import annotations

import re
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


DOCKER_COMPOSE = Path(__file__).parent.parent / "docker-compose.yml"
DOCKERFILE_COMPUTER = Path(__file__).parent.parent / "scripts" / "Dockerfile.computer"


def _parse_compose(path: Path) -> dict:
    """Parse docker-compose.yml without requiring PyYAML — uses a minimal regex approach
    for the specific checks we need, falling back to yaml if available."""
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(path.read_text())
    except ImportError:
        pass
    # Fallback: return raw text in a wrapper so callers can check containment
    return {"_raw": path.read_text()}


class TestDockerComposeComputerUseService:
    """Verify docker-compose.yml has a working computer-use service (issue #216).

    The computer-use service is the Docker/VNC streaming path described in
    issue #216 as the sandboxed deployment mode.  It must be present so users
    can run `docker compose up --build computer-use` to get a full sandboxed
    desktop environment with live VNC streaming.
    """

    def test_docker_compose_exists(self):
        assert DOCKER_COMPOSE.exists(), (
            f"docker-compose.yml not found: {DOCKER_COMPOSE}"
        )

    def test_dockerfile_computer_exists(self):
        assert DOCKERFILE_COMPUTER.exists(), (
            f"scripts/Dockerfile.computer not found: {DOCKERFILE_COMPUTER}"
        )

    def test_computer_use_service_present(self):
        """docker-compose.yml must define a computer-use service."""
        compose = _parse_compose(DOCKER_COMPOSE)
        if "_raw" in compose:
            assert "computer-use:" in compose["_raw"], (
                "docker-compose.yml has no 'computer-use' service. "
                "Add a computer-use service using scripts/Dockerfile.computer "
                "so users can run `docker compose up computer-use` for VNC streaming."
            )
        else:
            services = compose.get("services", {})
            assert "computer-use" in services, (
                f"docker-compose.yml services: {list(services)}. "
                "Missing 'computer-use' service. "
                "Add a computer-use service using scripts/Dockerfile.computer."
            )

    def test_computer_use_dockerfile_is_dockerfile_computer(self):
        """The computer-use service must reference scripts/Dockerfile.computer."""
        compose = _parse_compose(DOCKER_COMPOSE)
        if "_raw" in compose:
            assert "Dockerfile.computer" in compose["_raw"], (
                "docker-compose.yml does not reference Dockerfile.computer for computer-use. "
                "The computer-use service needs the Xvfb/VNC/xdotool image."
            )
        else:
            service = compose.get("services", {}).get("computer-use", {})
            build = service.get("build", {})
            dockerfile = build.get("dockerfile", "")
            assert "Dockerfile.computer" in dockerfile, (
                f"computer-use service uses '{dockerfile}', expected scripts/Dockerfile.computer. "
                "The Dockerfile.computer image provides Xvfb, VNC, xdotool, noVNC."
            )

    def _computer_use_service_block(self, raw: str) -> str:
        """Extract the computer-use service block from raw YAML text."""
        match = re.search(r"computer-use:.*?(?=\n\S|\Z)", raw, re.DOTALL)
        return match.group(0) if match else ""

    def test_novnc_port_exposed(self):
        """The computer-use service must expose port 6080 for the noVNC web interface."""
        compose = _parse_compose(DOCKER_COMPOSE)
        if "_raw" in compose:
            service_block = self._computer_use_service_block(compose["_raw"])
            assert "6080" in service_block, (
                "docker-compose.yml computer-use service does not expose port 6080 (noVNC). "
                "Users need port 6080 to view the live desktop via their browser."
            )
        else:
            service = compose.get("services", {}).get("computer-use", {})
            ports = service.get("ports", [])
            port_strs = [str(p) for p in ports]
            assert any("6080" in p for p in port_strs), (
                f"computer-use ports: {ports}. Port 6080 (noVNC) must be exposed."
            )

    def test_gptme_server_port_exposed(self):
        """The computer-use service must expose port 8080 for the gptme server."""
        compose = _parse_compose(DOCKER_COMPOSE)
        if "_raw" in compose:
            service_block = self._computer_use_service_block(compose["_raw"])
            assert "8080" in service_block, (
                "docker-compose.yml computer-use service does not expose port 8080 (gptme server). "
                "Port 8080 provides the chat interface and REST API for computer-use."
            )
        else:
            service = compose.get("services", {}).get("computer-use", {})
            ports = service.get("ports", [])
            port_strs = [str(p) for p in ports]
            assert any("8080" in p for p in port_strs), (
                f"computer-use ports: {ports}. "
                "Port 8080 (gptme server) must be exposed."
            )

    def test_computer_use_in_profile_not_default(self):
        """computer-use must be in a Docker Compose profile so it doesn't start by default.

        Running `docker compose up` should start only the headless gptme-server.
        The heavy computer-use container (Xvfb + VNC + browser) should only start
        when explicitly requested via `docker compose up computer-use`.
        """
        compose = _parse_compose(DOCKER_COMPOSE)
        if "_raw" in compose:
            # The profiles: key must appear somewhere in the computer-use block.
            # We check by looking for 'profiles' after 'computer-use:' in the raw YAML.
            raw = compose["_raw"]
            # Find the computer-use service block and ensure 'profiles' appears in it
            match = re.search(r"computer-use:.*?(?=\n\S|\Z)", raw, re.DOTALL)
            service_block = match.group(0) if match else ""
            assert "profiles" in service_block, (
                "computer-use service has no 'profiles' key. "
                "Add `profiles: [computer-use]` to prevent it from starting with plain "
                "`docker compose up` (which should start only gptme-server)."
            )
        else:
            service = compose.get("services", {}).get("computer-use", {})
            profiles = service.get("profiles", [])
            assert profiles, (
                "computer-use service has no profiles configured. "
                "Add `profiles: [computer-use]` to keep it out of the default `docker compose up`."
            )

    def test_api_keys_passed_to_computer_use(self):
        """computer-use service must forward provider API keys as environment variables."""
        compose = _parse_compose(DOCKER_COMPOSE)
        if "_raw" in compose:
            raw = compose["_raw"]
            match = re.search(r"computer-use:.*?(?=\n\S|\Z)", raw, re.DOTALL)
            service_block = match.group(0) if match else ""
            assert (
                "ANTHROPIC_API_KEY" in service_block
                or "OPENAI_API_KEY" in service_block
            ), (
                "computer-use service does not forward any provider API keys. "
                "At least one of ANTHROPIC_API_KEY or OPENAI_API_KEY must be in the environment."
            )
        else:
            service = compose.get("services", {}).get("computer-use", {})
            env = service.get("environment", {})
            env_keys = list(env.keys()) if isinstance(env, dict) else env
            assert any(
                k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")
                for k in env_keys
            ), (
                f"computer-use environment: {env_keys}. "
                "Must forward at least one provider API key."
            )


COMPUTER_HOME = Path(__file__).parent.parent / "scripts" / "computer_home"
XDEFAULTS = COMPUTER_HOME / ".Xdefaults"
XVFB_STARTUP = COMPUTER_HOME / "xvfb_startup.sh"
TINT2_STARTUP = COMPUTER_HOME / "tint2_startup.sh"
X11VNC_STARTUP = COMPUTER_HOME / "x11vnc_startup.sh"
NOVNC_STARTUP = COMPUTER_HOME / "novnc_startup.sh"


class TestTerminalStartupDelayFix:
    """Verify the fix for terminal startup delays in the Docker computer-use setup.

    gptme/gptme#216 identified that new terminal windows in Xvfb/X11 environments
    start slowly (1-3 seconds) due to xterm's default Xft font rendering scanning
    all system font directories (fontconfig scan).

    The fix has two parts:
    1. ~/.Xdefaults with XTerm*font: fixed — uses the built-in bitmap font,
       bypassing Xft/fontconfig and cutting xterm startup from ~2 s to < 100 ms.
    2. fc-cache -f in the Dockerfile — warms the fontconfig cache at build time
       so even the Xft path is fast if any other app triggers a scan.
    3. Faster polling (0.1 s) in startup scripts so container startup is quicker.
    """

    def test_xdefaults_exists(self):
        """~/.Xdefaults must exist in scripts/computer_home/ so it is COPY'd into the image."""
        assert XDEFAULTS.exists(), (
            f"scripts/computer_home/.Xdefaults not found: {XDEFAULTS}. "
            "This file is required to configure xterm's font and fix terminal startup delays."
        )

    def test_xdefaults_has_bitmap_font(self):
        """~/.Xdefaults must set XTerm*font to 'fixed' to bypass the Xft/fontconfig scan.

        Using the built-in 'fixed' bitmap font avoids the 1-3 second fontconfig
        directory scan that caused the new-terminal delay reported in gptme/gptme#216.
        """
        text = XDEFAULTS.read_text()
        assert "XTerm*font:" in text, (
            "~/.Xdefaults is missing an XTerm*font setting. "
            "Add 'XTerm*font: fixed' to use the built-in bitmap font."
        )
        # Extract the font value and check it is 'fixed' (or a bitmap family).
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("XTerm*font:"):
                font_value = stripped.split(":", 1)[1].strip()
                assert font_value == "fixed", (
                    f"XTerm*font is set to '{font_value}', expected 'fixed'. "
                    "The 'fixed' bitmap font is built into the X server and requires "
                    "no fontconfig scan, giving < 100 ms xterm startup."
                )
                break

    def test_xvfb_startup_loads_xdefaults(self):
        """xvfb_startup.sh must load ~/.Xdefaults via xrdb so xterm picks up the font setting."""
        text = XVFB_STARTUP.read_text()
        assert "xrdb" in text and ".Xdefaults" in text, (
            "xvfb_startup.sh does not load ~/.Xdefaults via xrdb. "
            "Add: xrdb -merge ~/.Xdefaults\n"
            "This applies the XTerm*font: fixed setting so every xterm in the "
            "container starts with the bitmap font."
        )

    def test_dockerfile_warms_fontconfig_cache(self):
        """Dockerfile.computer must run fc-cache -f to warm the font cache at build time.

        Even when xterm uses 'fixed', other apps may trigger a fontconfig scan.
        Running fc-cache -f once during the build ensures the cache is warm
        so any first-launch font scan completes quickly at runtime.
        """
        text = DOCKERFILE_COMPUTER.read_text()
        assert "fc-cache" in text, (
            "Dockerfile.computer does not run fc-cache to warm the fontconfig cache. "
            "Add 'RUN fc-cache -f' before the USER switch so font scans at runtime are fast."
        )

    def test_tint2_startup_uses_fast_polling(self):
        """tint2_startup.sh must poll with 0.1 s intervals, not 1 s.

        1-second polling intervals add up to several seconds of unnecessary
        startup time in the container. 0.1 s intervals give the same safety
        with ~10x less overhead.
        """
        text = TINT2_STARTUP.read_text()
        assert "sleep 1\n" not in text, (
            "tint2_startup.sh uses 'sleep 1' polling. "
            "Replace with 'sleep 0.1' so container startup is ~10x faster."
        )
        assert "sleep 0.1" in text, (
            "tint2_startup.sh must poll with 'sleep 0.1' for fast startup."
        )

    def test_x11vnc_startup_uses_fast_polling(self):
        """x11vnc_startup.sh must poll with 0.1 s intervals, not 1 s."""
        text = X11VNC_STARTUP.read_text()
        assert "sleep 1\n" not in text, (
            "x11vnc_startup.sh uses 'sleep 1' polling. "
            "Replace with 'sleep 0.1' so container startup is ~10x faster."
        )
        assert "sleep 0.1" in text, (
            "x11vnc_startup.sh must poll with 'sleep 0.1' for fast startup."
        )

    def test_novnc_startup_uses_fast_polling(self):
        """novnc_startup.sh must poll with 0.1 s intervals, not 1 s."""
        text = NOVNC_STARTUP.read_text()
        assert "sleep 1\n" not in text, (
            "novnc_startup.sh uses 'sleep 1' polling. "
            "Replace with 'sleep 0.1' so container startup is ~10x faster."
        )
        assert "sleep 0.1" in text, (
            "novnc_startup.sh must poll with 'sleep 0.1' for fast startup."
        )
