"""
Agent management module for gptme.

This module provides tools for setting up and managing autonomous gptme agents
across different platforms (systemd on Linux, launchd on macOS).
"""

from .service import ServiceManager, detect_service_manager
from .workspace import (
    WorkspaceError,
    create_workspace_from_template,
    create_workspace_structure,
    generate_run_script,
    init_conversation,
    write_agent_config,
)

__all__ = [
    "detect_service_manager",
    "ServiceManager",
    # Workspace functions
    "WorkspaceError",
    "create_workspace_from_template",
    "create_workspace_structure",
    "generate_run_script",
    "init_conversation",
    "write_agent_config",
]
