"""
Agent management module for gptme.

This module provides tools for setting up and managing autonomous gptme agents
across different platforms (systemd on Linux, launchd on macOS).
"""

from .service import ServiceManager, detect_service_manager

__all__ = ["detect_service_manager", "ServiceManager"]
