"""Hook system for skills (Phase 4.2).

Provides infrastructure for skills to define hooks that execute at specific
points in the skill lifecycle.
"""

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HookContext:
    """Context passed to hook functions.

    Attributes:
        skill: The Lesson object representing the skill
        message: Current message being processed (optional)
        conversation: Current conversation context (optional)
        tools: Available tools (optional)
        config: gptme configuration (optional)
        extra: Additional context data
    """

    skill: Any  # Lesson type, avoid circular import
    message: str | None = None
    conversation: Any | None = None
    tools: list[Any] | None = None
    config: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None


class HookManager:
    """Manages hook registration and execution for skills."""

    # Valid hook types
    VALID_HOOKS = {
        "pre_execute",
        "post_execute",
        "on_error",
        "pre_context",
        "post_context",
    }

    def __init__(self) -> None:
        """Initialize hook manager."""
        self._hooks: dict[str, list[tuple[Any, Path]]] = {
            hook_type: [] for hook_type in self.VALID_HOOKS
        }
        self._loaded_modules: dict[Path, Any] = {}

    def register_skill_hooks(self, skill: Any) -> None:
        """Register all hooks from a skill.

        Args:
            skill: Lesson object with metadata.hooks
        """
        if not skill.metadata.hooks:
            return

        for hook_type, hook_path in skill.metadata.hooks.items():
            if hook_type not in self.VALID_HOOKS:
                logger.warning(
                    f"Unknown hook type '{hook_type}' in skill {skill.title}. "
                    f"Valid types: {', '.join(self.VALID_HOOKS)}"
                )
                continue

            # Resolve hook path relative to skill directory
            skill_dir = skill.path.parent
            full_hook_path = skill_dir / hook_path

            if not full_hook_path.exists():
                logger.error(
                    f"Hook script not found: {full_hook_path} "
                    f"(referenced in {skill.title})"
                )
                continue

            # Register hook
            self._hooks[hook_type].append((skill, full_hook_path))
            logger.debug(
                f"Registered {hook_type} hook for {skill.title}: {hook_path}"
            )

    def execute_hooks(
        self, hook_type: str, context: HookContext
    ) -> list[Exception]:
        """Execute all hooks of a given type.

        Args:
            hook_type: Type of hook to execute
            context: Context to pass to hooks

        Returns:
            List of exceptions that occurred during execution (empty if all succeeded)
        """
        if hook_type not in self.VALID_HOOKS:
            raise ValueError(
                f"Invalid hook type '{hook_type}'. "
                f"Valid types: {', '.join(self.VALID_HOOKS)}"
            )

        errors: list[Exception] = []
        hooks = self._hooks.get(hook_type, [])

        logger.debug(f"Executing {len(hooks)} {hook_type} hook(s)")

        for skill, hook_path in hooks:
            try:
                self._execute_hook(hook_path, context)
            except Exception as e:
                logger.error(
                    f"Error executing {hook_type} hook for {skill.title}: {e}",
                    exc_info=True,
                )
                errors.append(e)
                # Continue with other hooks despite failure

        return errors

    def _execute_hook(self, hook_path: Path, context: HookContext) -> None:
        """Execute a single hook script.

        Args:
            hook_path: Path to hook script
            context: Context to pass to hook
        """
        # Load or reuse module
        if hook_path not in self._loaded_modules:
            module = self._load_module(hook_path)
            self._loaded_modules[hook_path] = module
        else:
            module = self._loaded_modules[hook_path]

        # Execute hook function
        if hasattr(module, "execute"):
            module.execute(context)
        else:
            logger.warning(
                f"Hook script {hook_path} missing execute() function. "
                "Hook will not run."
            )

    def _load_module(self, script_path: Path) -> Any:
        """Load a Python script as a module.

        Args:
            script_path: Path to Python script

        Returns:
            Loaded module
        """
        module_name = f"gptme_skill_hook_{script_path.stem}_{id(script_path)}"
        spec = importlib.util.spec_from_file_location(module_name, script_path)

        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load hook script: {script_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        return module

    def clear_hooks(self, hook_type: str | None = None) -> None:
        """Clear registered hooks.

        Args:
            hook_type: Specific hook type to clear, or None to clear all
        """
        if hook_type is None:
            for hooks in self._hooks.values():
                hooks.clear()
            self._loaded_modules.clear()
        elif hook_type in self.VALID_HOOKS:
            self._hooks[hook_type].clear()
        else:
            raise ValueError(f"Invalid hook type: {hook_type}")

    def get_registered_hooks(self, hook_type: str | None = None) -> dict[str, int]:
        """Get count of registered hooks.

        Args:
            hook_type: Specific hook type to query, or None for all

        Returns:
            Dictionary mapping hook type to count
        """
        if hook_type is None:
            return {ht: len(hooks) for ht, hooks in self._hooks.items()}
        elif hook_type in self.VALID_HOOKS:
            return {hook_type: len(self._hooks[hook_type])}
        else:
            raise ValueError(f"Invalid hook type: {hook_type}")


# Global hook manager instance
_hook_manager = HookManager()


def get_hook_manager() -> HookManager:
    """Get the global hook manager instance."""
    return _hook_manager
