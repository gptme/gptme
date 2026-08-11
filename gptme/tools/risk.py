"""Risk tier classification for gptme tool calls.

Provides a rule-based risk classifier that categorizes tool calls into
three tiers, enabling auto-approval of low-risk reads in interactive mode
and appropriate gating of destructive operations.

Shell/bash classification is delegated to ``shell_allowlist_hook``
(gptme/hooks/shell_validation.py), which already maintains a shell-command
allowlist for safe reads.  This module classifies non-shell tools only.

V1 is entirely rule-based. V2 can swap in a small classifier once behavioral
data accumulates.
"""

from __future__ import annotations

import re
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import ToolUse


class RiskTier(IntEnum):
    """Risk tiers for tool execution.

    READ (1): Safe, read-only operations — file reads, status queries, web search.
    Can be auto-approved in interactive mode without prompting.

    WRITE (2): State-modifying but reversible — file writes, git add, pip install.
    Standard confirmation in interactive mode; auto-approved with --no-confirm.

    DESTRUCTIVE (3): Hard-to-reverse or external write operations — rm, git push,
    sudo, network writes. Always prompts even in relaxed modes.
    """

    READ = 1
    WRITE = 2
    DESTRUCTIVE = 3


# Tools that are always safe reads
_READ_ONLY_TOOLS = frozenset(
    {
        "read",
        "rag",
        "web_search",
        "vision",
        "screenshot",
    }
)

# Tools that always require destructive-tier consideration
_DESTRUCTIVE_TOOLS = frozenset(
    {
        "computer",
        "tmux",
        "shell_background",
        "subagent",
    }
)


def classify_tool_risk(tool_use: ToolUse) -> RiskTier:
    """Classify the risk tier of a tool use.

    Args:
        tool_use: The tool use to classify.

    Returns:
        RiskTier.READ for safe read-only operations (auto-approvable).
        RiskTier.WRITE for state-modifying but reversible operations.
        RiskTier.DESTRUCTIVE for hard-to-reverse or external write operations.
    """
    tool = tool_use.tool
    content = tool_use.content or ""

    # Always-read tools
    if tool in _READ_ONLY_TOOLS:
        return RiskTier.READ

    # Always-destructive tools (full system access, spawns processes)
    if tool in _DESTRUCTIVE_TOOLS:
        return RiskTier.DESTRUCTIVE

    # Write/patch tools — moderate risk, content is the diff
    if tool in ("write", "patch", "save", "append", "patch_anchored", "patch_many"):
        return RiskTier.WRITE

    # Python/IPython execution — can do anything, but typically computation or
    # constrained workspace ops; treat as WRITE unless we detect something worse
    if tool in ("python", "ipython"):
        return RiskTier.WRITE

    # Browser — reads by default; posting/submitting is write
    if tool == "browser":
        if re.search(
            r"\b(?:submit|click|fill|type|press|post)\b", content, re.IGNORECASE
        ):
            return RiskTier.WRITE
        return RiskTier.READ

    # Shell/bash — always WRITE; shell_allowlist_hook handles the safe-read
    # short-circuit for specific allowlisted commands before this tier check runs.
    if tool in ("shell", "bash"):
        return RiskTier.WRITE

    # Default: WRITE (unknown tools are assumed to modify state)
    return RiskTier.WRITE
