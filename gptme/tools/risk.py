"""Risk tier classification for gptme tool calls.

Provides a rule-based risk classifier that categorizes tool calls into
three tiers, enabling auto-approval of low-risk reads in interactive mode
and appropriate gating of destructive operations.

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

# Output redirection that writes to a file (> or >>, but not >&2/>&1/>>>)
# Conservative: > /dev/null is also flagged; that's rare in genuine read-only work.
_SHELL_WRITE_REDIRECT = re.compile(r"(?<![<>|&])>{1,2}(?![>&])")

# `find` actions that cause mutations, even though `find` itself is a read-only prefix.
# -delete removes files; -exec/-execdir/-ok/-okdir run arbitrary commands on matches;
# -fls/-fprint/-fprint0/-fprintf write to a file.
_FIND_MUTATING_FLAGS = re.compile(
    r"\s+-(?:delete|exec(?:dir)?|ok(?:dir)?|fls\b|fprint[02]?\b|fprintf\b)",
    re.IGNORECASE,
)

# Command separators: splits a shell line into atomic sub-commands
_SHELL_CMD_SEP = re.compile(r"\s*(?:&&|\|\|?|;)\s*")

# Command/process substitution: $(...), backticks, or <(...) can hide state-changing ops inside a
# safe outer command.  e.g. `echo $(touch /tmp/created)` or `cat <(rm -rf /tmp/x)` has a safe
# prefix but the nested command executes unconditionally.
_SHELL_CMD_SUBST = re.compile(r"\$\(|`|<\(")

# Leading environment-variable assignments (e.g. TMPDIR=/tmp VAR=val) that precede the actual command.
_ENV_VAR_PREFIX = re.compile(r"(?:[A-Z_]+=\S+\s+)*")

# Env-var-prefixed git is unsafe: GIT_EXTERNAL_DIFF, GIT_SSH_COMMAND, GIT_EXEC_PATH,
# GIT_PAGER, GIT_ASKPASS etc. can redirect even "safe" git subcommands to external helpers.
_ENV_PREFIXED_GIT = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+git\b")

# Shell/bash commands whose first token indicates a safe read-only operation
# We match the start of the command (ignoring leading whitespace and env var assignments)
_SAFE_SHELL_CMDS = re.compile(
    r"(?:^|\n)\s*(?:[A-Z_]+=\S+\s+)*"  # optional env var prefix
    r"(?:"
    r"cat\b|head\b|tail\b|tac\b"
    r"|ls\b|ll\b|la\b|exa\b|eza\b"
    r"|pwd\b|echo\b|printf\b"
    r"|grep\b|rg\b|ripgrep\b|ag\b|ack\b"
    r"|wc\b|diff\b|colordiff\b"
    r"|find\b|locate\b|which\b|type\b|command\s+-v\b"
    r"|file\b|stat\b|du\b(?!\s+--delete|\s+-d\s+\S*d)"
    r"|df\b|free\b|uptime\b|uname\b|hostname\b|date\b|who\b|whoami\b"
    r"|ps\b|pgrep\b|jobs\b"
    r"|printenv\b"
    r"|jq\b|python3?\s+-m\s+json\b"
    r"|curl\s+(?:-s\s+|--silent\s+)?https?://[^\s]+(?:\s+-[svo]+)*$"
    r"|git\s+(?:status|log|diff|show|branch|tag|remote\s+-v|stash\s+(?:list|show)|"
    r"describe|rev-parse|rev-list\s+-n\b|shortlog|"
    r"--no-pager\s+(?:status|log|diff|show|branch))\b"
    r"|gh\s+(?:issue|pr|repo|release)\s+(?:view|list)\b"
    r"|true\b|false\b|:"
    r")",
    re.MULTILINE | re.IGNORECASE,
)

# Patterns in shell content that indicate destructive or external write operations
_DESTRUCTIVE_SHELL_PATTERNS = re.compile(
    r"(?:"
    # File deletion
    r"\brm\s+(?:-[a-z]*[rf][a-z]*|--force|--recursive)\b"
    r"|\brm\s+.*--force\b"
    r"|\brmdir\b"
    r"|\bshred\b|\bwipefs\b|\bsecure-delete\b"
    # Git push / force operations
    r"|\bgit\s+push\b"
    r"|\bgit\s+(?:reset\s+--hard|clean\s+-[a-z]*f[a-z]*|checkout\s+--)\b"
    r"|\bgit\s+rebase\s+(?!--abort|--continue|--status)\S"
    r"|\bgit\s+(?:push\s+--force|force-push)\b"
    # Privilege escalation
    r"|\bsudo\b|\bsu\s+(?:-|root)\b"
    r"|\bdoas\b"
    # Low-level disk operations
    r"|\bdd\s+\b|\bmkfs\.\w+|\bformat\b"
    # Credential/secret operations
    r"|\bpass\s+\b|\bsecret(?:tool|s)\s+\b"
    r"|\bkeychain\b|\bkwallet\b"
    # Network writes (curl/wget with POST/PUT/DELETE/PATCH or data upload)
    r"|\bcurl\s+(?:[^|&;\n]*(?:-X\s+(?:POST|PUT|DELETE|PATCH)|--data\b|--upload-file\b|"
    r"-d\s+|-F\s+|--form\s+))"
    r"|\bwget\s+(?:[^|&;\n]*(?:--post-data\b|--post-file\b))"
    # Package manager mutations (installs that affect system, not venv)
    r"|\bpip\s+(?:install|uninstall)\s+(?:--system\b|(?!.*--user\b)(?!.*venv)(?!.*\.venv))"
    r"|\bapt(?:-get)?\s+(?:install|remove|purge|upgrade)\b"
    r"|\byum\s+(?:install|remove|erase)\b"
    r"|\bbrew\s+(?:install|uninstall|upgrade)\b"
    r"|\bsnap\s+(?:install|remove)\b"
    r")",
    re.IGNORECASE,
)


def _is_safe_shell_line(line: str) -> bool:
    """Return True only if every sub-command in *line* is a safe READ-tier op.

    A line is unsafe if it contains output redirection (> or >>) or if any
    sub-command obtained by splitting on |, ;, &&, || doesn't match the
    safe-command prefix list.  Splitting on | catches pipe-to-write cases like
    ``cat file | tee /tmp/out`` without blocking safe pipe chains like
    ``grep foo | head -10``.
    """
    # Output redirection always produces write side-effects
    if _SHELL_WRITE_REDIRECT.search(line):
        return False
    # Command substitution ($(...) or backticks) can hide state-changing ops inside a
    # safe-looking outer command: e.g. `echo $(touch /tmp/created)` passes the `echo`
    # prefix check but the nested command executes unconditionally.
    if _SHELL_CMD_SUBST.search(line):
        return False
    # Split into sub-commands and validate each one
    parts = [p.strip() for p in _SHELL_CMD_SEP.split(line) if p.strip()]
    if not parts:
        return False
    for p in parts:
        if not _SAFE_SHELL_CMDS.match(p):
            return False
        # env-var-prefixed git: GIT_EXTERNAL_DIFF and similar vars redirect even
        # safe-looking git subcommands to execute arbitrary external helpers.
        if _ENV_PREFIXED_GIT.match(p.lstrip()):
            return False
        # `find` is in the safe-prefix list for plain queries, but certain flags
        # make it state-changing: -delete removes files, -exec/-execdir/-ok/-okdir
        # run arbitrary commands, -fls/-fprint* write to a file.
        # Strip leading env-var assignments (e.g. TMPDIR=/tmp find ...) before
        # checking the command name, so env-prefixed find calls are caught too.
        p_core = _ENV_VAR_PREFIX.sub("", p, count=1).lstrip()
        if re.match(r"find\b", p_core, re.IGNORECASE) and _FIND_MUTATING_FLAGS.search(
            p
        ):
            return False
    return True


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

    # Shell/bash — content-based classification
    if tool in ("shell", "bash"):
        # Check destructive patterns first (they take priority over safe prefixes)
        if _DESTRUCTIVE_SHELL_PATTERNS.search(content):
            return RiskTier.DESTRUCTIVE
        # Check if the entire command (multi-line) only uses safe reads
        lines = [
            ln.strip()
            for ln in content.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if lines and all(_is_safe_shell_line(ln) for ln in lines):
            return RiskTier.READ
        return RiskTier.WRITE

    # Default: WRITE (unknown tools are assumed to modify state)
    return RiskTier.WRITE
