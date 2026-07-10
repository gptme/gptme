"""Configuration dataclass models.

All configuration dataclasses used across gptme,
including their serialization helpers (from_dict, to_dict, merge).
"""

import logging
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from typing_extensions import Self

from ..context.config import ContextConfig
from ..context.selector.config import ContextSelectorConfig
from ..util import path_with_tilde

if TYPE_CHECKING:
    from .core import Config

logger = logging.getLogger(__name__)

VALID_PROJECT_SYSTEM_PROMPTS = {"full", "short"}


def _pop_object_section(config_data: dict, key: str) -> dict:
    """Pop a nested config section and require it to be an object."""
    value = config_data.pop(key, None)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _build_section(section_name: str, section_cls, section_data: dict):
    """Construct a config dataclass and normalize constructor errors."""
    try:
        return section_cls(**section_data)
    except TypeError as exc:
        raise ValueError(f"invalid {section_name} config: {exc}") from exc


@dataclass
class PluginsConfig:
    """Configuration for the plugin system."""

    # Plugin search paths (relative to config directory or absolute)
    paths: list[str] = field(default_factory=list)

    # Optional: plugin allowlist (empty = all discovered plugins enabled)
    enabled: list[str] = field(default_factory=list)


@dataclass
class MCPServerConfig:
    """Configuration for a MCP server."""

    name: str
    enabled: bool = True
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    url: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def is_http(self) -> bool:
        """Check if this is an HTTP MCP server."""
        return bool(self.url and self.url.startswith(("http://", "https://")))


@dataclass
class ProviderConfig:
    """Configuration for a custom OpenAI-compatible provider."""

    name: str
    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None
    default_model: str | None = None

    def get_api_key(self, config: "Config") -> str:
        """Get the API key from direct value or environment variable."""
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return config.get_env_required(self.api_key_env)
        # Default to provider name in uppercase
        return (
            config.get_env(f"{self.name.upper().replace('-', '_')}_API_KEY")
            or "default-key"
        )


@dataclass
class MCPConfig:
    """Configuration for :ref:`Model Context Protocol <MCP>` support, including which MCP servers to use."""

    enabled: bool = False
    auto_start: bool = False
    servers: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> Self:
        """Create a MCPConfig instance from a dictionary. Warns about unknown keys."""
        enabled = doc.pop("enabled", False)
        auto_start = doc.pop("auto_start", False)
        raw_servers = doc.pop("servers", [])
        if not isinstance(raw_servers, list):
            raise ValueError("mcp.servers must be a list")
        servers: list[MCPServerConfig] = []
        for server in raw_servers:
            if not isinstance(server, dict):
                raise ValueError("mcp.servers entries must be objects")
            try:
                servers.append(MCPServerConfig(**server))
            except (TypeError, ValueError) as e:
                raise ValueError(f"mcp.servers entry invalid: {e}") from e
        if doc:
            logger.warning(f"Unknown keys in MCP config: {doc.keys()}")
        return cls(
            enabled=enabled,
            auto_start=auto_start,
            servers=servers,
        )


@dataclass
class UserPromptConfig:
    """User-level configuration for user-specific prompts and project descriptions."""

    about_user: str | None = None
    response_preference: str | None = None
    project: dict[str, str] = field(default_factory=dict)
    # Additional files to include in context. Supports:
    # - Absolute paths
    # - ~ expansion
    # - Relative paths (resolved against the config directory, e.g. ~/.config/gptme)
    files: list[str] = field(default_factory=list)


@dataclass
class UserIdentityConfig:
    """Configuration for user identity."""

    name: str = "User"
    about: str | None = None
    response_preference: str | None = None
    avatar: str | None = None


@dataclass
class ModelsConfig:
    """Model-related user preferences, stored under a ``[models]`` section.

    Scope: these refer to the primary chat/LLM model (the same notion as the
    ``MODEL`` env var). Other modalities (TTS/STT/image) are configured
    separately; if they ever need formal model selection they should get their
    own scoped keys (e.g. ``[models.tts]``) rather than overloading these.
    """

    # Default chat model (fully-qualified id). Formal alternative to the MODEL
    # env var; takes precedence over MODEL but below an explicit per-chat model.
    default: str | None = None

    # User-curated favorite models (fully-qualified ids, e.g.
    # "anthropic/claude-opus-4-8"). Surfaced prominently in model pickers.
    favorites: list[str] = field(default_factory=list)


@dataclass
class SettingsConfig:
    """Project/user settings that affect CLI defaults."""

    gear: int | None = None


@dataclass
class UserConfig:
    """User-level configuration, such as user-specific prompts and environment variables."""

    prompt: UserPromptConfig = field(default_factory=UserPromptConfig)
    user: UserIdentityConfig = field(default_factory=UserIdentityConfig)

    env: dict[str, str] = field(default_factory=dict)
    mcp: MCPConfig | None = None
    providers: list[ProviderConfig] = field(default_factory=list)
    lessons: "LessonsConfig | None" = None

    # Model-related preferences (favorites, ...), under a [models] section.
    models: "ModelsConfig" = field(default_factory=lambda: ModelsConfig())

    # Plugin system configuration (search paths + enabled allowlist).
    # Layered with project-level [plugins] (see Config.get_plugin_config).
    plugins: PluginsConfig = field(default_factory=PluginsConfig)

    # CLI/runtime defaults. Mirrors project-level [settings].
    settings: SettingsConfig = field(default_factory=SettingsConfig)

    # Plugin-specific configuration namespace (user-level)
    # Allows plugins to have their own config sections like [plugin.retrieval]
    plugin: dict[str, dict] = field(default_factory=dict)


@dataclass
class RagConfig:
    """Configuration for :ref:`retrieval-augmented generation <RAG>` support."""

    enabled: bool = False
    max_tokens: int | None = None
    min_relevance: float | None = None
    post_process: bool = True
    post_process_model: str | None = None
    post_process_prompt: str | None = None
    workspace_only: bool = True
    paths: list[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    """Configuration for agent-specific settings."""

    name: str
    avatar: str | None = None
    urls: dict[str, str] | None = None
    """Named URLs for the agent, e.g. ``{"dashboard": "https://...", "repo": "https://..."}``.

    Configured in ``gptme.toml`` as an ``[agent.urls]`` section::

        [agent.urls]
        dashboard = "https://myagent.github.io/dashboard/"
        repo      = "https://github.com/myorg/myagent"
    """


@dataclass
class LessonsConfig:
    """Configuration for the lessons system."""

    dirs: list[str] = field(default_factory=list)


@dataclass
class ArchitectConfig:
    """Configuration for architect/editor coder split.

    When enabled, planning (architect model) and editing (editor model) are
    split into separate turns. The architect model produces a natural-language
    plan without tools, then the editor model executes against the plan.
    """

    enabled: bool = False
    """Enable architect/editor split mode."""

    architect_model: str | None = None
    """Model to use for the planning turn. Falls back to the primary model."""

    editor_model: str | None = None
    """Model to use for the editing turn. Falls back to a cheaper fast model."""

    auto_accept: bool = False
    """Skip user confirmation between architect and editor turns."""


@dataclass
class SubagentConfig:
    """Configuration for subagent execution."""

    max_concurrent: int | None = None
    """Maximum number of concurrently running subagents.

    Controls how many subagents can run simultaneously. Excess subagents queue
    and start as slots free up. Resolution order: ``GPTME_SUBAGENT_MAX_CONCURRENT``
    env var > this field > ``min(8, cpu_count)`` default.

    Example ``gptme.toml``::

        [subagent]
        max_concurrent = 4
    """


@dataclass
class ProjectConfig:
    """Project-level configuration, such as which files to include in the context by default.

    This is loaded from a gptme.toml :ref:`project-config` file in the project directory or .github directory.
    """

    _workspace: Path | None = None

    base_prompt: str | None = None
    prompt: str | None = None
    # System prompt type: "full" (default) or "short" (compact, ~60% fewer tokens).
    # Overridden by the --system CLI flag when explicitly passed.
    system: str | None = None
    files: list[str] | None = None
    exclude: list[str] = field(default_factory=list)
    context_cmd: str | None = None
    rag: RagConfig = field(default_factory=RagConfig)
    agent: AgentConfig | None = None
    lessons: LessonsConfig = field(default_factory=LessonsConfig)

    # Unified context configuration (replaces GPTME_FRESH + context_selector)
    context: ContextConfig = field(default_factory=ContextConfig)

    plugins: PluginsConfig = field(default_factory=PluginsConfig)

    architect: ArchitectConfig = field(default_factory=ArchitectConfig)

    subagent: SubagentConfig = field(default_factory=SubagentConfig)

    settings: SettingsConfig = field(default_factory=SettingsConfig)

    # Plugin-specific configuration namespace
    # Allows plugins to have their own config sections like [plugin.retrieval]
    # These are preserved as raw dicts for plugins to validate and use
    plugin: dict[str, dict] = field(default_factory=dict)

    env: dict[str, str] = field(default_factory=dict)
    mcp: MCPConfig | None = None

    @classmethod
    def from_dict(cls, config_data: dict, workspace: Path | None = None) -> Self:
        """Create a ProjectConfig instance from a dictionary. Warns about unknown keys."""
        # Support new "prompt" section or old-style base_prompt + files + context_cmd
        # Support new "prompt" section or old-style base_prompt + files + context_cmd
        prompt_data = config_data.pop("prompt", None)
        system: str | None = None
        if isinstance(prompt_data, dict):
            # New format: [prompt] section with nested values
            prompt = prompt_data.pop("prompt", None)
            system = prompt_data.pop("system", None)
            if system is not None and (
                not isinstance(system, str)
                or system not in VALID_PROJECT_SYSTEM_PROMPTS
            ):
                valid = ", ".join(sorted(VALID_PROJECT_SYSTEM_PROMPTS))
                raise ValueError(f"prompt.system must be one of: {valid}")
            base_prompt = prompt_data.pop("base_prompt", None)
            files = prompt_data.pop("files", None)
            exclude = prompt_data.pop("exclude", [])
            context_cmd = prompt_data.pop("context_cmd", None)
        else:
            # Old format: flat structure, prompt_data contains the prompt string
            prompt = prompt_data
            base_prompt = config_data.pop("base_prompt", None)
            files = config_data.pop("files", None)
            exclude = []
            context_cmd = config_data.pop("context_cmd", None)

        rag = _build_section("rag", RagConfig, _pop_object_section(config_data, "rag"))

        agent_data = config_data.pop("agent", None)
        if agent_data is not None and not isinstance(agent_data, dict):
            raise ValueError("agent must be an object")
        agent = (
            _build_section("agent", AgentConfig, agent_data)
            if agent_data is not None
            else None
        )

        lessons_data = _pop_object_section(config_data, "lessons")
        lessons = LessonsConfig(dirs=lessons_data.get("dirs", []))

        # Handle unified context config (replaces GPTME_FRESH + context_selector)
        # Support both old and new config formats for backward compatibility
        context_data = _pop_object_section(config_data, "context")
        context_selector_data = _pop_object_section(config_data, "context_selector")

        # If new [context] section exists, use it
        if context_data:
            context = ContextConfig.from_dict(context_data)
        # Otherwise, migrate old config format
        elif context_selector_data:
            # Create ContextConfig with selector from old config
            selector = ContextSelectorConfig.from_dict(context_selector_data)
            context = ContextConfig(enabled=False, selector=selector)
        else:
            # No config, use defaults
            context = ContextConfig()

        plugins_data = _pop_object_section(config_data, "plugins")
        plugins = PluginsConfig(
            paths=plugins_data.get("paths", []),
            enabled=plugins_data.get("enabled", []),
        )
        env = _pop_object_section(config_data, "env")
        mcp: MCPConfig | None = None
        mcp_data = config_data.pop("mcp", None)
        if mcp_data is not None and not isinstance(mcp_data, dict):
            raise ValueError("mcp must be an object")
        if mcp_data:
            mcp = MCPConfig.from_dict(mcp_data)

        # Extract plugin-prefixed keys (e.g., [plugin.retrieval] -> plugin["retrieval"])
        # This allows plugins to have their own config sections without triggering warnings
        plugin_config: dict[str, dict] = {}
        if plugin_data := config_data.pop("plugin", None):
            # Handle [plugin] section with nested subsections
            if not isinstance(plugin_data, dict):
                raise ValueError("plugin must be an object")
            plugin_config = plugin_data

        # Parse architect config from TOML dict
        architect = ArchitectConfig()
        if architect_data := config_data.pop("architect", None):
            if not isinstance(architect_data, dict):
                raise ValueError("architect must be an object")
            known_keys = set(ArchitectConfig.__dataclass_fields__)
            unknown = {k for k in architect_data if k not in known_keys}
            if unknown:
                logger.warning(f"Unknown keys in architect config: {unknown} (ignored)")
            architect = ArchitectConfig(
                **{k: v for k, v in architect_data.items() if k in known_keys}
            )

        subagent = _build_section(
            "subagent", SubagentConfig, _pop_object_section(config_data, "subagent")
        )

        settings = _build_section(
            "settings", SettingsConfig, _pop_object_section(config_data, "settings")
        )
        if settings.gear is not None:
            from ..gears import parse_gear

            try:
                settings.gear = parse_gear(settings.gear)
            except ValueError as exc:
                raise ValueError(f"settings.gear {exc}") from exc

        # Warn about unknown keys and drop them instead of passing them through
        # as kwargs (which would crash with "unexpected keyword argument").
        if config_data:
            logger.warning(
                f"Unknown keys in project config: {list(config_data.keys())} (ignored)"
            )

        return cls(
            _workspace=workspace,
            prompt=prompt,
            system=system,
            base_prompt=base_prompt,
            files=files,
            exclude=exclude,
            context_cmd=context_cmd,
            rag=rag,
            agent=agent,
            lessons=lessons,
            context=context,
            architect=architect,
            plugins=plugins,
            plugin=plugin_config,
            env=env,
            mcp=mcp,
            subagent=subagent,
            settings=settings,
        )

    def merge(self, other: Self) -> Self:
        """Merge another ProjectConfig into this one."""
        return replace(self, **dict(other.to_dict().items()))

    def to_dict(self) -> dict:
        """Convert ProjectConfig to a dictionary. Returns a dict with non-'mcp' and non-'env' keys nested under a 'project' key, and 'env' and 'mcp' as top-level keys."""

        # Custom function to handle Path objects during serialization
        def _dict_factory(items):
            result = {}
            for key, value in items:
                if isinstance(value, Path):
                    result[key] = str(path_with_tilde(value))
                else:
                    result[key] = value
            return result

        # Convert to dict and remove None values (including in nested dicts), using custom dict factory to handle Path objects
        def remove_none_values(d):
            if not isinstance(d, dict):
                return d
            return {k: remove_none_values(v) for k, v in d.items() if v is not None}

        config_dict = remove_none_values(
            {
                k: v
                for k, v in asdict(self, dict_factory=_dict_factory).items()
                if not k.startswith("_")
            }
        )

        return config_dict
