"""Tests for agent profiles functionality."""

import pytest

from gptme.profiles import (
    BUILTIN_PROFILES,
    Profile,
    ProfileBehavior,
    _load_markdown_profiles,
    _parse_markdown_profile,
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


class TestMarkdownProfiles:
    """Tests for markdown profile loading."""

    def test_parse_markdown_profile(self, tmp_path):
        """Parse a well-formed markdown profile."""
        profile_md = tmp_path / "test.md"
        profile_md.write_text(
            "---\n"
            "name: code-reviewer\n"
            "description: Reviews code for quality and correctness\n"
            "tools:\n"
            "  - read\n"
            "  - shell\n"
            "behavior:\n"
            "  read_only: true\n"
            "---\n"
            "\n"
            "# Code Reviewer\n"
            "\n"
            "You review code for quality, correctness, and style.\n"
        )

        profile = _parse_markdown_profile(profile_md)

        assert profile.name == "code-reviewer"
        assert profile.description == "Reviews code for quality and correctness"
        assert profile.tools == ["read", "shell"]
        assert profile.behavior.read_only is True
        assert profile.behavior.no_network is False
        assert "Code Reviewer" in profile.system_prompt
        assert "review code" in profile.system_prompt

    def test_parse_markdown_profile_minimal(self, tmp_path):
        """Parse a markdown profile with only required fields."""
        profile_md = tmp_path / "minimal.md"
        profile_md.write_text(
            "---\n"
            "name: minimal\n"
            "description: Minimal profile\n"
            "---\n"
            "\n"
            "Just a basic prompt.\n"
        )

        profile = _parse_markdown_profile(profile_md)

        assert profile.name == "minimal"
        assert profile.description == "Minimal profile"
        assert profile.tools is None  # default: all tools
        assert profile.system_prompt == "Just a basic prompt."

    def test_parse_markdown_profile_empty_body(self, tmp_path):
        """Markdown profile with no body has empty system_prompt."""
        profile_md = tmp_path / "empty.md"
        profile_md.write_text(
            "---\nname: empty-body\ndescription: No system prompt\n---\n"
        )

        profile = _parse_markdown_profile(profile_md)

        assert profile.name == "empty-body"
        assert profile.system_prompt == ""

    def test_parse_markdown_profile_missing_name(self, tmp_path):
        """Missing name field raises ValueError."""
        profile_md = tmp_path / "bad.md"
        profile_md.write_text("---\ndescription: No name\n---\nBody text\n")

        with pytest.raises(ValueError, match="missing required 'name'"):
            _parse_markdown_profile(profile_md)

    def test_parse_markdown_profile_missing_description(self, tmp_path):
        """Missing description field raises ValueError."""
        profile_md = tmp_path / "bad.md"
        profile_md.write_text("---\nname: no-desc\n---\nBody text\n")

        with pytest.raises(ValueError, match="missing required 'description'"):
            _parse_markdown_profile(profile_md)

    def test_parse_markdown_profile_no_frontmatter(self, tmp_path):
        """File without frontmatter raises ValueError."""
        profile_md = tmp_path / "nofm.md"
        profile_md.write_text("# Just markdown\n\nNo frontmatter here.\n")

        with pytest.raises(ValueError, match="must start with YAML frontmatter"):
            _parse_markdown_profile(profile_md)

    def test_load_markdown_profiles(self, tmp_path):
        """Load multiple markdown profiles from a directory."""
        (tmp_path / "alpha.md").write_text(
            "---\n"
            "name: alpha\n"
            "description: Alpha profile\n"
            "tools:\n"
            "  - read\n"
            "---\n"
            "\n"
            "Alpha instructions.\n"
        )
        (tmp_path / "beta.md").write_text(
            "---\nname: beta\ndescription: Beta profile\n---\n\nBeta instructions.\n"
        )
        # Non-md file should be ignored
        (tmp_path / "ignored.txt").write_text("not a profile")

        profiles = _load_markdown_profiles(tmp_path)

        assert len(profiles) == 2
        assert "alpha" in profiles
        assert "beta" in profiles
        assert profiles["alpha"].tools == ["read"]
        assert profiles["beta"].tools is None

    def test_load_markdown_profiles_skips_invalid(self, tmp_path):
        """Invalid markdown profiles are skipped with a warning."""
        (tmp_path / "good.md").write_text(
            "---\nname: good\ndescription: Valid profile\n---\n\nGood prompt.\n"
        )
        (tmp_path / "bad.md").write_text("---\nnot_a_name: oops\n---\n\nBad profile.\n")

        profiles = _load_markdown_profiles(tmp_path)

        assert len(profiles) == 1
        assert "good" in profiles

    def test_markdown_profile_full_behavior(self, tmp_path):
        """All behavior fields are parsed correctly."""
        profile_md = tmp_path / "strict.md"
        profile_md.write_text(
            "---\n"
            "name: strict\n"
            "description: Strict profile\n"
            "behavior:\n"
            "  read_only: true\n"
            "  no_network: true\n"
            "  confirm_writes: true\n"
            "---\n"
            "\n"
            "Strict mode.\n"
        )

        profile = _parse_markdown_profile(profile_md)

        assert profile.behavior.read_only is True
        assert profile.behavior.no_network is True
        assert profile.behavior.confirm_writes is True
