"""Agent profiles for pre-configured system prompts and tool access.

Profiles combine:
- System prompt customization
- Tool access restrictions
- Behavior rules

This enables creating specialized agents like "explorer" (read-only),
"researcher" (web access), or "developer" (full capabilities).
"""

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from .dirs import get_config_dir

logger = logging.getLogger(__name__)


@dataclass
class ProfileBehavior:
    """Behavior rules for a profile."""

    # If True, require confirmation for write operations
    confirm_writes: bool = False
    # If True, prevent all file writes
    read_only: bool = False
    # If True, prevent network access (browser, etc.)
    no_network: bool = False
    # Maximum context tokens (None = use model default)
    max_context_tokens: int | None = None


@dataclass
class Profile:
    """Agent profile combining system prompt, tools, and behavior rules.

    Attributes:
        name: Unique profile identifier
        description: Human-readable description
        system_prompt: Additional system prompt text (appended to base)
        tools: List of allowed tools (None = all tools, empty = no tools)
        behavior: Behavior rules for the profile
    """

    name: str
    description: str
    system_prompt: str = ""
    tools: list[str] | None = None
    behavior: ProfileBehavior = field(default_factory=ProfileBehavior)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        """Create a Profile from a dictionary (e.g., TOML config)."""
        behavior_data = data.pop("behavior", {})
        behavior = ProfileBehavior(**behavior_data)
        return cls(behavior=behavior, **data)


# Built-in profiles
BUILTIN_PROFILES: dict[str, Profile] = {
    "default": Profile(
        name="default",
        description="Full capabilities - standard gptme experience",
        system_prompt="",
        tools=None,  # All tools allowed
        behavior=ProfileBehavior(),
    ),
    "explorer": Profile(
        name="explorer",
        description="Read-only exploration - cannot modify files or access network",
        system_prompt=(
            "You are in EXPLORER mode. Your purpose is to understand and analyze, "
            "not to modify. You should:\n"
            "- Read and analyze files to understand the codebase\n"
            "- Search for patterns and gather information\n"
            "- Provide insights and recommendations\n"
            "- NOT modify any files or make changes\n"
            "- NOT access the network or external resources\n"
        ),
        tools=["read", "shell", "chats"],  # shell for read-only commands
        behavior=ProfileBehavior(read_only=True, no_network=True),
    ),
    "researcher": Profile(
        name="researcher",
        description="Web research - can browse but not modify local files",
        system_prompt=(
            "You are in RESEARCHER mode. Your purpose is to gather information "
            "from the web and provide analysis. You should:\n"
            "- Browse websites and search for information\n"
            "- Analyze and synthesize findings\n"
            "- Provide well-sourced answers\n"
            "- NOT modify local files (reports via output only)\n"
        ),
        tools=["browser", "read", "screenshot", "chats"],
        behavior=ProfileBehavior(read_only=True),
    ),
    "developer": Profile(
        name="developer",
        description="Full development capabilities",
        system_prompt=(
            "You are in DEVELOPER mode with full capabilities to:\n"
            "- Read, write, and modify files\n"
            "- Execute shell commands\n"
            "- Run code and tests\n"
            "- Use git and GitHub integration\n"
        ),
        tools=None,  # All tools
        behavior=ProfileBehavior(),
    ),
    "isolated": Profile(
        name="isolated",
        description="Isolated processing - no file writes or network (for untrusted content)",
        system_prompt=(
            "You are in ISOLATED mode for processing potentially untrusted content. "
            "You have restricted capabilities:\n"
            "- Read-only file access\n"
            "- No network access\n"
            "- No file modifications\n"
            "- Analyze and report only\n"
        ),
        tools=["read", "ipython"],  # ipython for computation only
        behavior=ProfileBehavior(read_only=True, no_network=True),
    ),
}


def get_user_profiles_dir() -> Path:
    """Get the directory for user-defined profiles."""
    return get_config_dir() / "profiles"


def load_user_profiles() -> dict[str, Profile]:
    """Load user-defined profiles from config directory.

    Profiles are stored as TOML files in ~/.config/gptme/profiles/
    """
    profiles: dict[str, Profile] = {}
    profiles_dir = get_user_profiles_dir()

    if not profiles_dir.exists():
        return profiles

    for profile_file in profiles_dir.glob("*.toml"):
        try:
            with open(profile_file, "rb") as f:
                data = tomllib.load(f)

            profile = Profile.from_dict(data)
            profiles[profile.name] = profile
            logger.debug(f"Loaded user profile: {profile.name}")
        except Exception as e:
            logger.warning(f"Failed to load profile {profile_file}: {e}")

    return profiles


def get_profile(name: str) -> Profile | None:
    """Get a profile by name.

    Checks user profiles first, then falls back to built-in profiles.
    """
    # Check user profiles first
    user_profiles = load_user_profiles()
    if name in user_profiles:
        return user_profiles[name]

    # Fall back to built-in profiles
    return BUILTIN_PROFILES.get(name)


def list_profiles() -> dict[str, Profile]:
    """List all available profiles (built-in and user-defined).

    User profiles override built-in profiles with the same name.
    """
    profiles = BUILTIN_PROFILES.copy()
    profiles.update(load_user_profiles())
    return profiles


def create_example_profile() -> None:
    """Create an example profile file in the user profiles directory."""
    profiles_dir = get_user_profiles_dir()
    profiles_dir.mkdir(parents=True, exist_ok=True)

    example_path = profiles_dir / "example.toml"
    if example_path.exists():
        return

    example_content = '''# Example custom profile
# Place .toml files in ~/.config/gptme/profiles/ to define custom profiles

name = "custom"
description = "My custom profile for specific tasks"

# Additional system prompt text (appended to base prompt)
system_prompt = """
You are a specialized assistant focused on Python development.
Follow PEP 8 style guidelines strictly.
"""

# List of allowed tools (omit for all tools)
tools = ["shell", "python", "save", "patch", "read", "gh"]

[behavior]
# Require confirmation for file writes
confirm_writes = false

# Prevent all file writes
read_only = false

# Prevent network access
no_network = false
'''

    with open(example_path, "w") as f:
        f.write(example_content)

    logger.info(f"Created example profile at {example_path}")
