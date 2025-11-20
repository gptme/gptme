"""Context management plugin system.

Provides extensible plugin interface for context transformation and management.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TransformResult:
    """Result of a context transformation.

    Attributes:
        content: Transformed content
        tokens_saved: Number of tokens saved (positive) or added (negative)
        metadata: Additional metadata about the transformation
    """

    content: str
    tokens_saved: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextPlugin(ABC):
    """Abstract base class for context management plugins.

    Plugins transform context in various ways (compression, augmentation,
    filtering, etc.) and can be composed into transformation pipelines.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the plugin identifier.

        Returns:
            Unique plugin name (e.g., 'compression', 'rag')
        """
        pass

    @abstractmethod
    def transform(self, content: str, context: dict[str, Any]) -> TransformResult:
        """Transform context content.

        Args:
            content: The content to transform
            context: Additional context for transformation (model, config, etc.)

        Returns:
            TransformResult with transformed content and metrics
        """
        pass

    def get_config_schema(self) -> dict[str, Any]:
        """Return the configuration schema for this plugin.

        Override this method to define plugin-specific configuration.
        Schema should be JSON-schema compatible.

        Returns:
            Configuration schema (empty dict by default)
        """
        return {}

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate plugin configuration.

        Override for custom validation logic.

        Args:
            config: Configuration to validate

        Returns:
            True if valid, False otherwise
        """
        return True
