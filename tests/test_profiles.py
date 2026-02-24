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

    def test_profile_from_dict_no_mutation(self):
        data = {
            "name": "custom",
            "description": "Custom profile",
            "behavior": {"read_only": True},
        }
        original_data = dict(data)

        Profile.from_dict(data)

        assert data == original_data
        assert "behavior" in data

    def test_profile_default_behavior(self):
        profile = Profile(name="test", description="Test")

        assert profile.behavior.read_only is False
        assert profile.behavior.no_network is False
        assert profile.behavior.confirm_writes is False


class TestBuiltinProfiles:
    """Tests for built-in profiles."""

    def test_default_profile_exists(self):
        assert "default" in BUILTIN_PROFILES

    def test_explorer_profile(self):
        explorer = BUILTIN_PROFILES["explorer"]

        assert explorer.name == "explorer"
        assert explorer.behavior.read_only is True
        assert explorer.behavior.no_network is True
        assert explorer.tools is not None
        assert "read" in explorer.tools

    def test_researcher_profile(self):
        researcher = BUILTIN_PROFILES["researcher"]

        assert researcher.name == "researcher"
        assert researcher.behavior.read_only is True
        assert researcher.behavior.no_network is False
        assert researcher.tools is not None
        assert "browser" in researcher.tools

    def test_isolated_profile(self):
        isolated = BUILTIN_PROFILES["isolated"]

        assert isolated.name == "isolated"
        assert isolated.behavior.read_only is True
        assert isolated.behavior.no_network is True

    def test_developer_profile(self):
        developer = BUILTIN_PROFILES["developer"]

        assert developer.name == "developer"
        assert developer.tools is None
        assert developer.behavior.read_only is False


class TestGetProfile:
    """Tests for get_profile function."""

    def test_get_builtin_profile(self):
        profile = get_profile("explorer")

        assert profile is not None
        assert profile.name == "explorer"

    def test_get_unknown_profile(self):
        profile = get_profile("nonexistent")

        assert profile is None


class TestValidateTools:
    """Tests for Profile.validate_tools method."""

    def test_validate_all_valid(self):
        profile = Profile(
            name="test",
            description="Test",
            tools=["read", "shell"],
        )
        unknown = profile.validate_tools({"read", "shell", "browser"})
        assert unknown == []

    def test_validate_unknown_tools(self):
        profile = Profile(
            name="test",
            description="Test",
            tools=["read", "nonexistent", "alsofake"],
        )
        unknown = profile.validate_tools({"read", "shell", "browser"})
        assert unknown == ["alsofake", "nonexistent"]

    def test_validate_none_tools(self):
        """Profile with tools=None (all tools) always validates."""
        profile = Profile(name="test", description="Test", tools=None)
        unknown = profile.validate_tools({"read", "shell"})
        assert unknown == []

    def test_validate_builtin_profiles(self):
        """All built-in profiles should reference valid tool names."""
        # Use a known set of tool names (superset of what profiles reference)
        known_tools = {
            "read",
            "save",
            "append",
            "shell",
            "ipython",
            "browser",
            "screenshot",
            "chats",
            "patch",
            "morph",
            "computer",
            "rag",
            "tmux",
            "vision",
            "youtube",
            "tts",
            "subagent",
            "gh",
            "complete",
            "choice",
            "form",
        }
        for name, profile in BUILTIN_PROFILES.items():
            unknown = profile.validate_tools(known_tools)
            assert unknown == [], (
                f"Built-in profile '{name}' has unknown tools: {unknown}"
            )


class TestListProfiles:
    """Tests for list_profiles function."""

    def test_list_includes_builtin(self):
        profiles = list_profiles()

        assert "default" in profiles
        assert "explorer" in profiles
        assert "researcher" in profiles
        assert "developer" in profiles
        assert "isolated" in profiles
