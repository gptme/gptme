"""Tests for tool risk tier classification."""

import pytest

from gptme.tools.base import ToolUse
from gptme.tools.risk import RiskTier, classify_tool_risk


def _tu(tool: str, content: str = "") -> ToolUse:
    """Helper to build a minimal ToolUse for testing."""
    return ToolUse(tool=tool, args=[], content=content)


# ── READ-tier tools ────────────────────────────────────────────────────────────


def test_read_tool_is_tier1() -> None:
    assert classify_tool_risk(_tu("read", "/etc/hostname")) == RiskTier.READ


def test_web_search_is_tier1() -> None:
    assert classify_tool_risk(_tu("web_search", "gptme docs")) == RiskTier.READ


def test_vision_is_tier1() -> None:
    assert classify_tool_risk(_tu("vision")) == RiskTier.READ


def test_rag_is_tier1() -> None:
    assert classify_tool_risk(_tu("rag", "search query")) == RiskTier.READ


def test_screenshot_is_tier1() -> None:
    assert classify_tool_risk(_tu("screenshot")) == RiskTier.READ


# ── WRITE-tier tools ───────────────────────────────────────────────────────────


def test_write_tool_is_tier2() -> None:
    assert classify_tool_risk(_tu("write", "new content")) == RiskTier.WRITE


def test_patch_tool_is_tier2() -> None:
    assert classify_tool_risk(_tu("patch", "+added line")) == RiskTier.WRITE


def test_save_tool_is_tier2() -> None:
    assert classify_tool_risk(_tu("save", "content")) == RiskTier.WRITE


def test_append_tool_is_tier2() -> None:
    assert classify_tool_risk(_tu("append", "more content")) == RiskTier.WRITE


def test_python_tool_is_tier2() -> None:
    assert classify_tool_risk(_tu("python", "x = 1 + 2")) == RiskTier.WRITE


# ── Shell — always WRITE; shell_allowlist_hook handles safe-read short-circuit ─


@pytest.mark.parametrize(
    "cmd",
    [
        "cat /etc/hosts",
        "git status",
        "gh pr list",
        "rm -rf /tmp/important",
        "git push origin master",
        "sudo apt install python3",
    ],
)
def test_shell_commands_are_always_write(cmd: str) -> None:
    """All shell commands classify as WRITE; content-based inspection is delegated
    to shell_allowlist_hook (shell_validation.py) which short-circuits before the
    tier check for allowlisted safe reads."""
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.WRITE, (
        f"Expected WRITE for: {cmd!r}"
    )


def test_bash_tool_is_write() -> None:
    assert classify_tool_risk(_tu("bash", "cat /etc/hosts")) == RiskTier.WRITE


# ── DESTRUCTIVE-tier tools ─────────────────────────────────────────────────────


def test_computer_tool_is_tier3() -> None:
    assert classify_tool_risk(_tu("computer")) == RiskTier.DESTRUCTIVE


def test_tmux_tool_is_tier3() -> None:
    assert classify_tool_risk(_tu("tmux", "rm -rf /")) == RiskTier.DESTRUCTIVE


# ── Browser ────────────────────────────────────────────────────────────────────


def test_browser_navigation_is_tier1() -> None:
    assert classify_tool_risk(_tu("browser", "https://docs.gptme.org")) == RiskTier.READ


def test_browser_form_submit_is_tier2() -> None:
    assert classify_tool_risk(_tu("browser", "click submit button")) == RiskTier.WRITE


# ── Unknown tool ───────────────────────────────────────────────────────────────


def test_unknown_tool_defaults_to_write() -> None:
    assert classify_tool_risk(_tu("mystery_tool", "content")) == RiskTier.WRITE


# ── RiskTier ordering ──────────────────────────────────────────────────────────


def test_risk_tiers_are_ordered() -> None:
    assert RiskTier.READ < RiskTier.WRITE < RiskTier.DESTRUCTIVE


def test_risk_tier_comparison_with_int() -> None:
    """The _AUTO_APPROVE_TIER_MAX constant (int) must compare correctly."""
    assert RiskTier.READ <= 1
    assert RiskTier.WRITE > 1
    assert RiskTier.DESTRUCTIVE > 1
