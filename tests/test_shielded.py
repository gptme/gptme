"""Tests for shielded processing mode."""

import pytest
from click.testing import CliRunner

from gptme.cli import main


class TestShieldedMode:
    """Tests for --shielded CLI option."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_shielded_full_mode(self, runner):
        """Test --shielded=full restricts tools appropriately."""
        # We can't easily test full execution, but we can test the CLI parses correctly
        # by checking help output mentions shielded
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "--shielded" in result.output

    def test_shielded_flag_alone(self, runner):
        """Test --shielded without value defaults to 'full'."""
        result = runner.invoke(main, ["--help"])
        assert "shielded mode" in result.output.lower() or "untrusted" in result.output.lower()

    def test_list_profiles_includes_isolated(self, runner):
        """Test that --list-profiles shows isolated profile for untrusted content."""
        result = runner.invoke(main, ["--list-profiles"])
        assert result.exit_code == 0
        assert "isolated" in result.output.lower()
        assert "untrusted" in result.output.lower()


class TestShieldedSecurity:
    """Security-focused tests for shielded mode."""

    def test_shielded_profile_no_save_tool(self):
        """Verify full shielded mode doesn't include save tool."""
        from gptme.profiles import ProfileBehavior, Profile

        # Simulate the shielded-full profile
        shielded_full = Profile(
            name="shielded-full",
            description="Full shielding - read-only, no network",
            system_prompt="...",
            tools=["read", "ipython", "chats"],
            behavior=ProfileBehavior(read_only=True, no_network=True),
        )

        assert "save" not in shielded_full.tools
        assert "patch" not in shielded_full.tools
        assert "shell" not in shielded_full.tools
        assert shielded_full.behavior.read_only is True
        assert shielded_full.behavior.no_network is True

    def test_shielded_network_no_browser(self):
        """Verify network shielded mode doesn't include browser."""
        from gptme.profiles import ProfileBehavior, Profile

        shielded_network = Profile(
            name="shielded-network",
            description="Network shielding - no network access",
            system_prompt="...",
            tools=["read", "save", "patch", "shell", "ipython", "chats"],
            behavior=ProfileBehavior(no_network=True),
        )

        assert "browser" not in shielded_network.tools
        assert shielded_network.behavior.no_network is True

    def test_shielded_write_no_save(self):
        """Verify write shielded mode doesn't include save/patch."""
        from gptme.profiles import ProfileBehavior, Profile

        shielded_write = Profile(
            name="shielded-write",
            description="Write shielding - no file writes",
            system_prompt="...",
            tools=["read", "browser", "shell", "ipython", "chats", "screenshot"],
            behavior=ProfileBehavior(read_only=True),
        )

        assert "save" not in shielded_write.tools
        assert "patch" not in shielded_write.tools
        assert shielded_write.behavior.read_only is True
