"""Tests for agent profiles functionality."""

from gptme.profiles import (
    BUILTIN_PROFILES,
    Profile,
    ProfileBehavior,
    get_profile,
    list_profiles,
)


class TestProfile:
    """Tests for Profile dataclass."""

    def test_profile_creation(self):
        """Test basic profile creation."""
        profile = Profile(
            name="test",
            description="Test profile",
            system_prompt="Test prompt",
            tools=["shell", "read"],
            behavior=ProfileBehavior(read_only=True),
        )

        assert profile.name == "test"
        assert profile.description == "Test profile"
        assert profile.system_prompt == "Test prompt"
        assert profile.tools == ["shell", "read"]
        assert profile.behavior.read_only is True
        assert profile.behavior.no_network is False

    def test_profile_from_dict(self):
        """Test creating profile from dict."""
        data = {
            "name": "custom",
            "description": "Custom profile",
            "system_prompt": "Custom prompt",
            "tools": ["browser"],
            "behavior": {"read_only": True, "no_network": True},
        }

        profile = Profile.from_dict(data)

        assert profile.name == "custom"
        assert profile.behavior.read_only is True
        assert profile.behavior.no_network is True

    def test_profile_default_behavior(self):
        """Test profile with default behavior."""
        profile = Profile(name="test", description="Test")

        assert profile.behavior.read_only is False
        assert profile.behavior.no_network is False
        assert profile.behavior.confirm_writes is False


class TestBuiltinProfiles:
    """Tests for built-in profiles."""

    def test_default_profile_exists(self):
        """Test that default profile exists."""
        assert "default" in BUILTIN_PROFILES

    def test_explorer_profile(self):
        """Test explorer profile configuration."""
        explorer = BUILTIN_PROFILES["explorer"]

        assert explorer.name == "explorer"
        assert explorer.behavior.read_only is True
        assert explorer.behavior.no_network is True
        assert explorer.tools is not None
        assert "read" in explorer.tools
        assert "shell" in explorer.tools

    def test_researcher_profile(self):
        """Test researcher profile configuration."""
        researcher = BUILTIN_PROFILES["researcher"]

        assert researcher.name == "researcher"
        assert researcher.behavior.read_only is True
        assert researcher.behavior.no_network is False
        assert researcher.tools is not None
        assert "browser" in researcher.tools

    def test_isolated_profile(self):
        """Test isolated profile for untrusted content."""
        isolated = BUILTIN_PROFILES["isolated"]

        assert isolated.name == "isolated"
        assert isolated.behavior.read_only is True
        assert isolated.behavior.no_network is True

    def test_developer_profile(self):
        """Test developer profile has full capabilities."""
        developer = BUILTIN_PROFILES["developer"]

        assert developer.name == "developer"
        assert developer.tools is None  # All tools allowed
        assert developer.behavior.read_only is False


class TestGetProfile:
    """Tests for get_profile function."""

    def test_get_builtin_profile(self):
        """Test getting a built-in profile."""
        profile = get_profile("explorer")

        assert profile is not None
        assert profile.name == "explorer"

    def test_get_unknown_profile(self):
        """Test getting an unknown profile returns None."""
        profile = get_profile("nonexistent")

        assert profile is None


class TestListProfiles:
    """Tests for list_profiles function."""

    def test_list_includes_builtin(self):
        """Test that list includes all built-in profiles."""
        profiles = list_profiles()

        assert "default" in profiles
        assert "explorer" in profiles
        assert "researcher" in profiles
        assert "developer" in profiles
        assert "isolated" in profiles
