"""Agent profiles for pre-configured system prompts and tool access.

Profiles combine:
- System prompt customization (behavioral guidance)
- Tool access restrictions (hard-enforced when used via subagent tool)
- Behavior rules (read-only, no-network, etc.)

Tool restrictions are hard-enforced in subagent thread mode: only allowed
tools are loaded into the execution context, so the LLM cannot call
restricted tools even if it tries. CLI mode (--agent-profile) also
hard-enforces via the tool allowlist. Behavior rules (read_only, no_network)
remain soft/prompting-based.

This enables creating specialized agents like "explorer" (read-only),
"researcher" (web access), or "developer" (full capabilities).

User profiles can be defined in ~/.config/gptme/profiles/ as either:
- TOML files (.toml): Traditional key-value format
- Markdown files (.md): YAML frontmatter for metadata, body as system_prompt
"""

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from .dirs import get_config_dir

logger = logging.getLogger(__name__)


@dataclass
class ProfileBehavior:
    """Behavior rules for a profile."""

    confirm_writes: bool = False
    read_only: bool = False
    no_network: bool = False


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
        """Create a Profile from a dictionary (e.g., TOML config).

        Note: This method does not mutate the input dictionary.
        """
        behavior_data = data.get("behavior", {})
        behavior = ProfileBehavior(**behavior_data)
        profile_data = {k: v for k, v in data.items() if k != "behavior"}

        # Validate tools field type: must be a list or None, not a bare string
        tools = profile_data.get("tools")
        if isinstance(tools, str):
            raise TypeError(
                f"Profile '{data.get('name', '?')}': "
                f"'tools' must be a list (e.g. ['read', 'shell']), got string '{tools}'"
            )

        return cls(behavior=behavior, **profile_data)

    def validate_tools(self, available_tool_names: set[str]) -> list[str]:
        """Validate that profile tool names exist in the available tools.

        Returns list of unknown tool names (empty if all valid).
        """
        if self.tools is None:
            return []
        return sorted(set(self.tools) - available_tool_names)


# Built-in profiles
BUILTIN_PROFILES: dict[str, Profile] = {
    "default": Profile(
        name="default",
        description="Full capabilities - standard gptme experience",
        system_prompt="",
        tools=None,
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
        tools=["read", "chats"],
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
            "- Use all available tools\n"
        ),
        tools=None,
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
        tools=["read", "ipython"],
        behavior=ProfileBehavior(read_only=True, no_network=True),
    ),
}


def get_user_profiles_dir() -> Path:
    """Get the directory for user-defined profiles."""
    return get_config_dir() / "profiles"


def _parse_markdown_profile(path: Path) -> Profile:
    """Parse a markdown file with YAML frontmatter into a Profile.

    Format::

        ---
        name: my-profile
        description: A custom profile
        tools:
          - read
          - shell
        behavior:
          read_only: false
        ---

        # My Profile

        You are a specialized agent...

    The YAML frontmatter contains profile metadata (name, description,
    tools, behavior). The markdown body becomes the system_prompt.
    """
    if not HAS_YAML:
        raise ImportError(
            "PyYAML is required for markdown profiles. Install with: pip install pyyaml"
        )

    content = path.read_text(encoding="utf-8")

    if not content.startswith("---"):
        raise ValueError(f"Markdown profile must start with YAML frontmatter: {path}")

    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Invalid YAML frontmatter in: {path}")

    frontmatter_str = parts[1]
    body = parts[2].strip()

    frontmatter = yaml.safe_load(frontmatter_str)
    if not frontmatter or not isinstance(frontmatter, dict):
        raise ValueError(f"Empty or invalid YAML frontmatter in: {path}")

    if "name" not in frontmatter:
        raise ValueError(f"Markdown profile missing required 'name' field: {path}")
    if "description" not in frontmatter:
        raise ValueError(
            f"Markdown profile missing required 'description' field: {path}"
        )

    # Build profile data dict: frontmatter fields + body as system_prompt
    data = dict(frontmatter)
    data["system_prompt"] = body

    return Profile.from_dict(data)


def _load_toml_profiles(profiles_dir: Path) -> dict[str, Profile]:
    """Load profiles from TOML files in a directory."""
    profiles: dict[str, Profile] = {}

    for profile_file in profiles_dir.glob("*.toml"):
        try:
            with open(profile_file, "rb") as f:
                data = tomllib.load(f)

            profile = Profile.from_dict(data)
            profiles[profile.name] = profile
            logger.debug("Loaded TOML profile: %s from %s", profile.name, profile_file)
        except Exception as e:
            logger.warning("Failed to load TOML profile %s: %s", profile_file, e)

    return profiles


def _load_markdown_profiles(profiles_dir: Path) -> dict[str, Profile]:
    """Load profiles from markdown files with YAML frontmatter."""
    profiles: dict[str, Profile] = {}

    for profile_file in profiles_dir.glob("*.md"):
        try:
            profile = _parse_markdown_profile(profile_file)
            profiles[profile.name] = profile
            logger.debug(
                "Loaded markdown profile: %s from %s", profile.name, profile_file
            )
        except Exception as e:
            logger.warning("Failed to load markdown profile %s: %s", profile_file, e)

    return profiles


def load_user_profiles() -> dict[str, Profile]:
    """Load user-defined profiles from config directory.

    Profiles are stored as TOML (.toml) or Markdown (.md) files
    in ~/.config/gptme/profiles/. Markdown files override TOML files
    with the same profile name.
    """
    profiles_dir = get_user_profiles_dir()

    if not profiles_dir.exists():
        return {}

    # Load TOML first, then markdown (markdown overrides on name collision)
    profiles = _load_toml_profiles(profiles_dir)
    profiles.update(_load_markdown_profiles(profiles_dir))

    return profiles


def get_profile(name: str) -> Profile | None:
    """Get a profile by name.

    Checks user profiles first, then falls back to built-in profiles.
    """
    user_profiles = load_user_profiles()
    if name in user_profiles:
        return user_profiles[name]

    return BUILTIN_PROFILES.get(name)


def list_profiles() -> dict[str, Profile]:
    """List all available profiles (built-in and user-defined).

    User profiles override built-in profiles with the same name.
    """
    profiles = BUILTIN_PROFILES.copy()
    profiles.update(load_user_profiles())
    return profiles
