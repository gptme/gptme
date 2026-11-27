"""GitHub integration tool.

Use native handlers only when they help the assistant succeed more reliably
than a raw ``gh`` command in the shell tool.  The native path is worth it when
it collapses several API calls into one response, keeps CI state structured and
actionable, or adds merge safety guards that are easy to miss in ad-hoc CLI use.

Native operations that materially help:

- ``issue view`` — combines issue body and comments in one call
- ``pr view``    — combines PR body, comments, review-thread resolution, CI,
                    and mergeability in one call
- ``pr status``  — structured check-run summary with actionable run IDs
- ``pr checks``  — polls CI until a failure, completion, or the foreground
                    wait budget, with live progress updates
- ``pr merge``   — squash default, ``--match-head-commit`` guard, auto-merge
- ``run view``   — extracts and structures failed log sections from CI runs

Adding a new native wrapper
---------------------------
Before wrapping a ``gh`` subcommand, ask: "Will this help the assistant do
better than a single ``gh`` command in the shell tool?"  If not, don't add it
— the pass-through already covers it without bloating instructions.

Good candidates combine multiple API calls into one response, add safety
guards, or poll/wait for completion.
"""

import json
import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import yaml

from ..message import Message
from ..util.gh import (
    DEFAULT_LOG_MAX_TOKENS,
    _get_commit_check_runs,
    _truncate_log_tail,
    get_github_issue_content,
    get_github_pr_content,
    get_github_run_logs,
    merge_github_pr,
    parse_github_ref,
    parse_github_url,
    redact_gh_output,
)
from ..util.workspace import get_workspace_cwd
from . import Parameter, ToolSpec, ToolUse

logger = logging.getLogger(__name__)


def has_gh_tool() -> bool:
    return shutil.which("gh") is not None


def _get_pr_check_runs(
    owner: str, repo: str, pr_number: int
) -> tuple[str | None, list | None, str | None]:
    """Get check runs for a PR.

    Returns:
        Tuple of (head_sha, check_runs, error_message)
    """
    try:
        # Get PR details to extract HEAD commit SHA
        pr_details_result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/pulls/{pr_number}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        pr_details = json.loads(pr_details_result.stdout)
        head_sha = pr_details.get("head", {}).get("sha")

        if not head_sha:
            return None, None, "Could not get HEAD commit SHA"

        check_runs = _get_commit_check_runs(owner, repo, head_sha)

        return head_sha, check_runs, None

    except subprocess.TimeoutExpired:
        return None, None, "GitHub API call timed out after 30s"
    except subprocess.CalledProcessError as e:
        return None, None, f"Failed to fetch check status: {e}"
    except (ValueError, KeyError) as e:
        return None, None, f"Failed to parse check data: {e}"


def _check_run_state(run: dict) -> str:
    """Classify checks explicitly; unknown terminal conclusions fail closed."""
    status = run.get("status")
    if status == "completed":
        conclusion = run.get("conclusion")
        if conclusion in ("success", "cancelled", "skipped", "neutral"):
            return conclusion
        return "failure"
    if status in ("queued", "pending", "in_progress", "waiting", "requested"):
        return "in_progress"
    return "failure"


def _failed_check_reference(run: dict) -> str:
    """Keep external check links; check-run IDs are not workflow run IDs."""
    name = f"{run.get('name', 'Unknown')} ({run.get('conclusion')})"
    url = run.get("html_url") or ""
    match = re.fullmatch(
        r"https://github\.com/[^/]+/[^/]+/actions/runs/(\d+)(?:[/?#].*)?", url
    )
    if match:
        run_id = match.group(1)
        return f"{name} (run {run_id}) — gh run view {run_id} --log-failed"
    return f"{name} — {url}" if url else name


_CHECKS_FOREGROUND_TIMEOUT_DEFAULT = 60.0
_CHECKS_FOREGROUND_TIMEOUT_ENV = "GPTME_GH_CHECKS_TIMEOUT"


def _get_checks_timeout() -> float:
    """Return the foreground wait budget for ``pr checks``, in seconds.

    Default is 60s so the tool can return a failure or a still-pending snapshot
    without pinning the conversation for 30 minutes. Set
    ``GPTME_GH_CHECKS_TIMEOUT=0`` to return after the first poll. Invalid
    values fall back to the default.
    """
    raw = os.environ.get(
        _CHECKS_FOREGROUND_TIMEOUT_ENV, str(_CHECKS_FOREGROUND_TIMEOUT_DEFAULT)
    )
    try:
        timeout = float(raw)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError
    except ValueError:
        logger.warning(
            "Invalid %s value: %s, using %gs",
            _CHECKS_FOREGROUND_TIMEOUT_ENV,
            raw,
            _CHECKS_FOREGROUND_TIMEOUT_DEFAULT,
        )
        return _CHECKS_FOREGROUND_TIMEOUT_DEFAULT
    return timeout


def _format_still_running(names: list[str]) -> str:
    """Summarize checks that have not finished yet."""
    if not names:
        return ""
    shown = ", ".join(names[:3])
    extra = len(names) - 3
    if extra > 0:
        shown = f"{shown} and {extra} more"
    return f"\nStill running: {shown}\n"


def _pending_timeout_message(
    max_wait: float,
    in_progress: int,
    status_parts: list[str],
    in_progress_runs: list[str],
) -> Message:
    """Foreground-budget snapshot so the caller can stop waiting."""
    timeout_msg = (
        f"\n⏱️ Stopped waiting after {max_wait:g}s with "
        f"{in_progress} checks still running.\n"
    )
    if status_parts:
        timeout_msg += f"{', '.join(status_parts)}\n"
    timeout_msg += _format_still_running(in_progress_runs)
    return Message("system", timeout_msg)


def _wait_for_checks(
    owner: str, repo: str, url: str, commit_sha: str | None = None
) -> Generator[Message, None, None]:
    """Wait until checks fail, complete, or the foreground wait budget elapses."""
    logger = logging.getLogger(__name__)

    # Use provided commit SHA or get from PR
    pr_number: int | None
    if commit_sha:
        head_sha: str | None = commit_sha
        pr_number = None  # Not needed when checking specific commit
    else:
        # Get PR details to extract HEAD commit SHA
        pr_info = parse_github_url(url)
        if not pr_info:
            yield Message("system", "Error: Could not parse PR number from URL")
            return

        pr_number = int(pr_info["number"])

        head_sha, check_runs_initial, error = _get_pr_check_runs(owner, repo, pr_number)
        if error:
            yield Message("system", f"Error: {error}")
            return

        if not check_runs_initial:
            yield Message("system", "No checks found for this PR")
            return

    assert head_sha is not None  # Ensured by earlier error check
    yield Message("system", f"Waiting for checks on commit {head_sha[:7]}...\n")

    if pr_number:
        logger.info(f"Polling PR #{pr_number} checks (commit {head_sha[:7]})...")
    else:
        logger.info(f"Polling checks for commit {head_sha[:7]}...")

    previous_status = None
    poll_interval = 10  # seconds
    max_wait = _get_checks_timeout()
    start_time = time.time()

    while True:
        # Get check runs for the original commit directly
        try:
            check_runs = _get_commit_check_runs(owner, repo, head_sha)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            ValueError,
            KeyError,
        ) as e:
            yield Message("system", f"Error fetching checks: {e}")
            return

        if not check_runs:
            yield Message("system", f"No checks found for commit {head_sha[:7]}")
            return

        # Group by status and conclusion, track failed and still-running checks
        status_counts = {
            "success": 0,
            "failure": 0,
            "cancelled": 0,
            "skipped": 0,
            "neutral": 0,
            "in_progress": 0,
        }

        failed_runs = []
        in_progress_runs: list[str] = []

        for run in check_runs:
            state = _check_run_state(run)

            # Preserve the original reason and the correct CI service reference.
            if state == "failure":
                failed_runs.append(_failed_check_reference(run))
            elif state == "in_progress":
                in_progress_runs.append(str(run.get("name", "Unknown")))

            status_counts[state] += 1

        # Create status summary
        current_status = {
            "total": len(check_runs),
            "in_progress": status_counts["in_progress"],
            "success": status_counts["success"],
            "failure": status_counts["failure"],
            "cancelled": status_counts["cancelled"],
            "skipped": status_counts["skipped"],
            "neutral": status_counts["neutral"],
        }

        status_parts = []
        if current_status["success"] > 0:
            status_parts.append(f"✅ {current_status['success']} passed")
        if current_status["failure"] > 0:
            status_parts.append(f"❌ {current_status['failure']} failed")
        if current_status["cancelled"] > 0:
            status_parts.append(f"🚫 {current_status['cancelled']} cancelled")
        if current_status["skipped"] > 0:
            status_parts.append(f"⏭️ {current_status['skipped']} skipped")
        if current_status["neutral"] > 0:
            status_parts.append(f"➖ {current_status['neutral']} neutral")
        if current_status["in_progress"] > 0:
            status_parts.append(f"🔄 {current_status['in_progress']} in progress")

        # Show update if status changed
        if current_status != previous_status:
            yield Message(
                "system",
                f"[{time.strftime('%H:%M:%S')}] {', '.join(status_parts)}\n",
            )
            previous_status = current_status

        # Return as soon as a failure is known, even if other jobs are pending.
        if current_status["failure"] > 0:
            failure_msg = (
                f"\n❌ Checks failed: {current_status['failure']} failed, "
                f"{current_status['success']} passed"
            )
            if current_status["in_progress"] > 0:
                failure_msg += f", {current_status['in_progress']} still running"
            failure_msg += "\n"

            if failed_runs:
                failure_msg += "\nFailed runs:\n"
                for reference in failed_runs:
                    failure_msg += f"  - {reference}\n"

            failure_msg += _format_still_running(in_progress_runs)
            yield Message("system", failure_msg)
            return

        # Check if all remaining checks are done
        if current_status["in_progress"] == 0:
            if current_status["cancelled"] > 0:
                yield Message(
                    "system",
                    f"\n🚫 Checks cancelled: {current_status['cancelled']} cancelled, {current_status['success']} passed\n",
                )
            elif current_status["neutral"] > 0 or current_status["skipped"] > 0:
                yield Message(
                    "system",
                    f"\nChecks complete: {current_status['success']} passed, "
                    f"{current_status['neutral']} neutral, {current_status['skipped']} skipped\n",
                )
            else:
                yield Message(
                    "system",
                    f"\n✅ All checks passed! ({current_status['success']} checks)\n",
                )
            return

        remaining = max_wait - (time.time() - start_time)
        if remaining <= 0:
            yield _pending_timeout_message(
                max_wait,
                current_status["in_progress"],
                status_parts,
                in_progress_runs,
            )
            return

        # Wait before next poll, without overshooting the foreground budget
        time.sleep(min(poll_interval, remaining))
        # Do not start another API poll after the budget is gone; a check-run
        # listing can take up to 30s per page.
        if time.time() - start_time >= max_wait:
            yield _pending_timeout_message(
                max_wait,
                current_status["in_progress"],
                status_parts,
                in_progress_runs,
            )
            return


def _format_check_results(check_runs: list, head_sha: str, pr_number: int) -> str:
    """Format check run results into a human-readable summary."""
    status_counts: dict[str, int] = {
        "success": 0,
        "failure": 0,
        "cancelled": 0,
        "skipped": 0,
        "neutral": 0,
        "in_progress": 0,
    }

    failed_runs: list[str] = []
    in_progress_runs: list[str] = []

    for run in check_runs:
        state = _check_run_state(run)
        run_name = run.get("name", "Unknown")

        if state == "failure":
            failed_runs.append(_failed_check_reference(run))
        elif state == "in_progress":
            in_progress_runs.append(run_name)

        status_counts[state] += 1

    total = len(check_runs)
    in_progress = status_counts["in_progress"]

    output = f"PR #{pr_number} checks ({head_sha[:7]}):\n"
    output += f"Total: {total} checks\n"

    if status_counts["success"] > 0:
        output += f"✅ {status_counts['success']} passed\n"
    if status_counts["failure"] > 0:
        output += f"❌ {status_counts['failure']} failed\n"
    if status_counts["cancelled"] > 0:
        output += f"🚫 {status_counts['cancelled']} cancelled\n"
    if status_counts["skipped"] > 0:
        output += f"⏭️ {status_counts['skipped']} skipped\n"
    if status_counts["neutral"] > 0:
        output += f"➖ {status_counts['neutral']} neutral\n"
    if in_progress > 0:
        output += f"🔄 {in_progress} in progress\n"

    if failed_runs:
        output += "\nFailed runs:\n"
        for reference in failed_runs:
            output += f"  - {reference}\n"

    if in_progress_runs:
        output += f"\nIn progress: {', '.join(in_progress_runs[:3])}"
        if len(in_progress_runs) > 3:
            output += f" and {len(in_progress_runs) - 3} more"
        output += "\n"

    return output


def _extract_url(
    args: list[str] | None, kwargs: dict[str, str] | None, arg_offset: int = 2
) -> str | None:
    """Extract a GitHub URL from args or kwargs."""
    if args and len(args) > arg_offset:
        return args[arg_offset]
    if kwargs:
        return kwargs.get("url", None)
    return None


def _resolve_ref(
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    default_type: str,
    entity_name: str = "reference",
) -> tuple[dict[str, str] | None, Message | None]:
    """Extract and parse a GitHub reference from args/kwargs.

    Returns (github_info, error_message). If github_info is None,
    the caller should yield the error message and return.
    """
    ref = _extract_url(args, kwargs)
    if not ref:
        return None, Message("system", f"Error: No {entity_name} provided")

    ref = ref.strip()
    # Match gh's repository selection, including GH_REPO and the default set
    # by `gh repo set-default` in fork checkouts. Never guess origin for a
    # mutation when gh cannot determine the intended repository.
    if re.fullmatch(r"#?\d+", ref):
        try:
            result = subprocess.run(
                [
                    "gh",
                    "repo",
                    "view",
                    "--json",
                    "url",
                    "--jq",
                    ".url",
                ],
                cwd=get_workspace_cwd(),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            repo_url = result.stdout.strip().rstrip("/")
            if not re.fullmatch(
                r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_url
            ):
                raise ValueError("Unsupported repository returned by gh")
            ref = f"{repo_url}/{default_type}/{ref.lstrip('#')}"
        except (subprocess.SubprocessError, OSError, ValueError):
            return None, Message(
                "system",
                "Error: Could not determine GitHub repository. Use owner/repo#N or --repo owner/repo.",
            )
    github_info = parse_github_ref(ref, default_type=default_type)
    if not github_info:
        return None, Message(
            "system",
            f"Error: Could not parse GitHub reference: {ref}\n\n"
            "Accepted formats: URL, owner/repo#N, #N, or N",
        )

    return github_info, None


def _handle_pr_status(
    args: list[str] | None, kwargs: dict[str, str] | None
) -> Generator[Message, None, None]:
    """Handle `gh pr status <ref> [commit_sha]` command."""
    info, err = _resolve_ref(args, kwargs, "pull", "PR reference")
    if err:
        yield err
        return

    assert info is not None
    commit_sha = args[3] if args and len(args) > 3 else None
    pr_number = int(info["number"])
    owner = info["owner"]
    repo = info["repo"]

    if commit_sha:
        try:
            check_runs = _get_commit_check_runs(owner, repo, commit_sha)
            head_sha = commit_sha
            error = None
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            ValueError,
            KeyError,
        ) as e:
            error = f"Failed to fetch checks: {e}"
            head_sha, check_runs = None, None
    else:
        head_sha, check_runs, error = _get_pr_check_runs(owner, repo, pr_number)

    if error:
        yield Message("system", f"Error: {error}")
        return

    if not check_runs:
        yield Message(
            "system",
            f"No checks found for commit {head_sha[:7] if head_sha else 'unknown'}",
        )
        return

    try:
        assert head_sha is not None
        yield Message("system", _format_check_results(check_runs, head_sha, pr_number))
    except (json.JSONDecodeError, KeyError) as e:
        yield Message("system", f"Error: Failed to parse check data: {e}")


def _handle_pr_merge(
    args: list[str] | None, kwargs: dict[str, str] | None
) -> Generator[Message, None, None]:
    """Handle `gh pr merge <ref> [--squash|--rebase|--merge] [--auto] [--delete-branch] [--match-head-commit SHA]`."""
    # A structured caller may provide its reference through the separate url
    # parameter, leaving options (rather than a reference) at index 2.
    has_ref = bool(args and len(args) > 2 and not args[2].startswith("-"))
    flags = args[3 if has_ref else 2 :] if args else []
    method: str | None = None
    auto = False
    delete_branch = False
    match_head: str | None = None

    i = 0
    while i < len(flags):
        arg = flags[i]
        if arg in ("--squash", "--rebase", "--merge"):
            if method is not None and method != arg[2:]:
                yield Message("system", "Error: Conflicting merge methods")
                return
            method = arg[2:]
        elif arg == "--auto":
            auto = True
        elif arg == "--delete-branch":
            delete_branch = True
        elif arg == "--match-head-commit" or arg.startswith("--match-head-commit="):
            if match_head is not None:
                yield Message("system", "Error: Repeated --match-head-commit guard")
                return
            if arg == "--match-head-commit":
                i += 1
                value = flags[i] if i < len(flags) else ""
            else:
                value = arg.split("=", 1)[1]
            if not value.strip() or value.startswith("-"):
                yield Message(
                    "system", "Error: --match-head-commit requires a SHA value"
                )
                return
            match_head = value
        else:
            yield Message("system", f"Error: Unsupported merge argument: {arg}")
            return
        i += 1

    info, err = _resolve_ref(args if has_ref else None, kwargs, "pull", "PR reference")
    if err:
        yield err
        return

    assert info is not None
    pr_number = info["number"]
    owner = info["owner"]
    repo = info["repo"]

    result = merge_github_pr(
        owner,
        repo,
        pr_number,
        method=method or "squash",
        auto=auto,
        delete_branch=delete_branch,
        match_head_commit=match_head,
    )

    if result["success"]:
        output = str(result["message"])
        if "sha" in result:
            output += f"\nMerge commit: {result['sha']}"
        yield Message("system", output)
    else:
        yield Message("system", f"Error: {result['message']}")


def _redact_passthrough_output(text: str) -> str:
    """Apply the shell's credential-redaction policy to gh tool output.

    A retained ``tool-outputs/gh/`` file must not hold what the rest of the
    pipeline redacts; see ``redact_gh_output`` for the policy.
    """
    return redact_gh_output(text)


def _save_oversized_output(text: str, command_info: str):
    """Save oversized pass-through output under the conversation's tool-outputs.

    Returns the saved path, or None without a conversation directory or when
    saving fails: the omission notice must still be delivered either way.
    """
    try:
        from ..logmanager import LogManager
        from ..util.output_storage import save_large_output

        manager = LogManager.get_current_log()
        if manager and manager.logdir:
            _, saved_path = save_large_output(
                content=text,
                logdir=manager.logdir,
                output_type="gh",
                command_info=command_info,
            )
            return saved_path
    except Exception as e:  # best-effort; the omission notice still applies
        logger.warning("Could not save full gh output: %s", e)
    return None


def _failure_stream_preview(
    text: str, max_chars: int, stream: str, cmd: list[str]
) -> str:
    """Tail-truncate a failed pass-through stream, saving it first if it is cut.

    The omission marker names the saved file when one was written; without a
    conversation directory, or if saving fails, it stays a plain count.
    """
    if len(text) <= max_chars:
        return text
    saved_path = _save_oversized_output(text, f"{shlex.join(cmd)} ({stream})")
    hint = f"full {stream} saved to {saved_path}" if saved_path is not None else ""
    return _truncate_log_tail(text, max_chars, hint)


# gh subcommands that make the CLI run a local program. The gh tool never asks
# for confirmation, so letting these through would hand the model unconfirmed
# local code execution outside the shell tool's confirmation and guardrails.
_LOCAL_PROGRAM_CONFIG_KEYS = frozenset({"browser", "editor", "pager"})
_SAFE_EXTENSION_ACTIONS = frozenset({"list", "ls", "search"})
_EXTENSION_RUN_ACTIONS = frozenset({"browse", "create", "exec", "install", "upgrade"})

# Commands gh itself implements. Anything else is a user alias or an installed
# extension, both of which run programs the CLI resolves at call time.
_GH_BUILTIN_COMMANDS = frozenset(
    {
        "agent-task",
        "alias",
        "api",
        "attestation",
        "auth",
        "browse",
        "cache",
        "codespace",
        "completion",
        "config",
        "copilot",
        "discussion",
        "extension",
        "gist",
        "gpg-key",
        "issue",
        "label",
        "licenses",
        "org",
        "pr",
        "preview",
        "project",
        "release",
        "repo",
        "ruleset",
        "run",
        "search",
        "secret",
        "skill",
        "ssh-key",
        "status",
        "variable",
        "workflow",
        # Command-group aliases gh ships with.
        "cs",
        "ext",
        "extensions",
    }
)


def _gh_config_dirs() -> list[Path]:
    """Candidate gh config directories.

    Windows resolves its config from ``%AppData%`` rather than XDG, so that
    location is included whenever the variable is set. Reading an extra path
    can only make the guard stricter; resolving the wrong single directory
    would let an existing alias through without confirmation.
    """
    if configured := os.environ.get("GH_CONFIG_DIR"):
        return [Path(configured)]
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    dirs = [Path(base) / "gh"]
    if appdata := os.environ.get("APPDATA"):
        dirs.append(Path(appdata) / "GitHub CLI")
    return dirs


def _gh_data_dirs() -> list[Path]:
    """Candidate gh data directories (extension installs). See config dirs."""
    if configured := os.environ.get("GH_DATA_DIR"):
        return [Path(configured)]
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = [Path(base) / "gh"]
    for var in ("LOCALAPPDATA", "APPDATA"):
        if value := os.environ.get(var):
            candidate = Path(value) / "GitHub CLI"
            if candidate not in dirs:
                dirs.append(candidate)
    return dirs


def _gh_alias_values() -> dict[str, str] | None:
    """Alias name -> definition from gh's config files.

    Returns an empty dict when no config file exists. Returns ``None`` when a
    config file exists but cannot be read or parsed: the guard then cannot
    tell whether a subcommand is an alias, so it must refuse rather than let
    an alias run unconfirmed.

    Read on every call instead of cached: an alias defined through the shell
    tool mid-session must not stay invisible to this guard, and a missing
    config must not stall the pass-through on a subprocess timeout.
    """
    aliases: dict[str, str] = {}
    for directory in _gh_config_dirs():
        path = directory / "config.yml"
        try:
            # gh itself reads the config as UTF-8. The locale default would
            # reject any non-ASCII alias on a cp1252 host and skip the whole
            # file, hiding every alias it defines.
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            # The file exists but cannot be read: refusing unknown
            # subcommands is the only fail-closed option.
            return None
        try:
            config = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
        found = config.get("aliases") if isinstance(config, dict) else None
        if isinstance(found, dict):
            aliases.update({str(name): str(value) for name, value in found.items()})
    return aliases


def _gh_extension_names() -> frozenset[str]:
    """Names of installed gh extensions, from ``gh-<name>`` entries on disk."""
    names: set[str] = set()
    for directory in _gh_data_dirs():
        try:
            entries = list((directory / "extensions").iterdir())
        except OSError:
            continue
        names.update(
            entry.name.removeprefix("gh-")
            for entry in entries
            if entry.name.startswith("gh-")
        )
    return frozenset(names)


def _local_exec_reason(
    gh_args: list[str], _seen: frozenset[str] = frozenset()
) -> str | None:
    """Return why ``gh <gh_args>`` would run a local program, or None.

    gh accepts flags between a command group and its subcommand, so the
    dangerous subcommands are matched among all positionals of their group.
    """
    if gh_args in (["--version"], ["--help"], ["-h"]):
        return None
    # Repository selection is an inherited flag accepted before the command.
    # Skip only known forms, not arbitrary flags whose values could conceal
    # which command gh resolves (including aliases and extensions).
    while gh_args and gh_args[0].startswith("-"):
        flag = gh_args[0]
        if flag in ("-R", "--repo") and len(gh_args) > 1:
            gh_args = gh_args[2:]
        elif flag.startswith("--repo=") or (flag.startswith("-R") and len(flag) > 2):
            gh_args = gh_args[1:]
        else:
            return "flags before the subcommand hide which command runs"
    positionals = [a for a in gh_args if not a.startswith("-")]
    if not positionals:
        return None
    sub, action = positionals[0], positionals[1] if len(positionals) > 1 else None
    rest = set(positionals[1:])
    # Any alias can later reach a refused command under another name.
    if sub == "alias" and rest & {"set", "import"}:
        return "an alias can run local programs or rename a refused command"
    if sub in ("extension", "extensions", "ext") and action is not None:
        if action not in _SAFE_EXTENSION_ACTIONS or rest & _EXTENSION_RUN_ACTIONS:
            return f"`gh {sub} {action}` installs or runs extension programs"
    if sub == "config" and "set" in rest and _LOCAL_PROGRAM_CONFIG_KEYS & rest:
        return "the browser, editor and pager settings are commands gh executes"
    if "--" in gh_args[:-1]:
        # ``-- <flags>`` reaches git or ssh, where ``-c core.sshCommand=`` or
        # ``-oProxyCommand=`` names a program that runs locally.
        if sub in ("repo", "gist") and rest & {"clone", "fork"}:
            return f"`gh {sub}` with `-- <gitflags>` passes options to git"
        if sub in ("codespace", "cs") and rest & {"ssh", "cp"}:
            return f"`gh {sub}` with `--` passes options to ssh"
    if sub in _GH_BUILTIN_COMMANDS:
        return None
    # An alias or an installed extension runs under a name none of the checks
    # above recognize. Refuse rather than hand the model unconfirmed local
    # execution; a plain alias that only expands to a safe gh command is fine.
    if sub in _seen:
        return "an alias cycle hides which command runs"
    alias_values = _gh_alias_values()
    if alias_values is None:
        return "gh's config exists but could not be parsed; cannot rule out an alias"
    alias_value = alias_values.get(sub)
    if alias_value is not None:
        if alias_value.startswith("!"):
            return f"`gh {sub}` is a shell alias, which runs local commands"
        try:
            expanded = shlex.split(alias_value)
        except ValueError:
            return f"`gh {sub}` is an alias with an unparsable definition"
        if reason := _local_exec_reason(expanded + gh_args[1:], _seen | {sub}):
            return f"`gh {sub}` is an alias for `gh {alias_value}`: {reason}"
    if sub in _gh_extension_names():
        return f"`gh {sub}` is an installed extension, which runs a local program"
    return None


def _passthrough_gh(
    args: list[str] | None, code: str | None
) -> Generator[Message, None, None]:
    """Pass unrecognized commands through to the gh CLI."""
    if args:
        cmd = ["gh"] + list(args)
    elif code:
        cmd_str = code.strip().removeprefix("gh ")
        try:
            cmd = ["gh"] + shlex.split(cmd_str)
        except ValueError as e:
            yield Message("system", f"Error parsing command: {e}")
            return
    else:
        yield Message("system", "Error: No command provided")
        return

    if reason := _local_exec_reason(cmd[1:]):
        yield Message(
            "system",
            f"Error: refused `{shlex.join(cmd)}`: {reason}. The gh tool does not "
            "run local programs; use the shell tool, which asks for confirmation.",
        )
        return

    logger.info("gh pass-through: %s", shlex.join(cmd))
    cwd = get_workspace_cwd()
    try:
        # gh infers the repository from its cwd: use the session's, not the
        # host process cwd (None keeps the CLI's process cwd).
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        max_chars = DEFAULT_LOG_MAX_TOKENS * 4
        # Redact before any preview or spill, so the retained file is the same
        # representation the model sees.
        stdout_text = _redact_passthrough_output((result.stdout or "").strip())
        stderr_text = _redact_passthrough_output((result.stderr or "").strip())
        if result.returncode != 0:
            # A failure keeps only each stream's tail inline. Save a stream whose
            # head was dropped and name the file in its omission marker, so the
            # earlier output stays readable without re-running the command.
            stderr = _failure_stream_preview(stderr_text, max_chars, "stderr", cmd)
            output = _failure_stream_preview(stdout_text, max_chars, "stdout", cmd)
            details = []
            if stderr:
                details.append(f"stderr:\n{stderr}")
            if output:
                details.append(f"stdout:\n{output}")
            msg = f"Error (exit {result.returncode})"
            if details:
                msg += "\n\n" + "\n\n".join(details)
            yield Message("system", msg)
        elif len(stdout_text) > max_chars:
            saved_path = _save_oversized_output(stdout_text, shlex.join(cmd))
            if saved_path is not None:
                where = (
                    f"The full output was saved to {saved_path}; read it "
                    "selectively (tail, grep, jq). "
                )
            else:
                where = (
                    "Inspect the result with a read-only query using --jq/--limit, "
                    "or use the shell tool to redirect a read-only query to a file. "
                )
            yield Message(
                "system",
                "Command succeeded (exit 0), but stdout exceeded the response limit "
                f"and was omitted. {where}Do not repeat a mutating command: its "
                "changes have already been applied.",
            )
        elif stdout_text:
            yield Message("system", stdout_text)
        else:
            yield Message("system", "(no output)")
    except subprocess.TimeoutExpired:
        yield Message("system", "Error: Command timed out after 60 seconds")
    except FileNotFoundError as exc:
        if cwd is not None and exc.filename == cwd:
            yield Message("system", f"Error: working directory not found: {cwd}")
        else:
            yield Message(
                "system",
                "Error: gh CLI not found. Install from https://cli.github.com/",
            )


# gh documents ``[HOST/]OWNER/REPO``. The native handlers only talk to
# github.com, so that host prefix (bare or as a URL) is stripped; any other
# host is rejected rather than silently resolved against github.com.
_REPO_RE = re.compile(
    r"^(?:(?:https?://)?github\.com/)?([\w.-]+/[\w.-]+?)/?$", re.IGNORECASE
)


def _parse_run_repo(flags: list[str]) -> tuple[str | None, str | None]:
    """Extract an explicit ``--repo``/``-R`` value from ``gh run view`` flags.

    Accepts ``--repo X``, ``--repo=X``, ``-R X``, ``-RX`` and ``-R=X``; the last
    occurrence wins, as in the gh CLI.  Returns ``(repo, error)``; ``repo`` is
    None when no flag was given.
    """
    repo: str | None = None
    i = 0
    while i < len(flags):
        flag = flags[i]
        i += 1
        value: str | None
        if flag in ("--repo", "-R"):
            value = flags[i] if i < len(flags) else ""
            i += 1
        elif flag.startswith("--repo="):
            value = flag[len("--repo=") :]
        elif flag.startswith("-R"):
            value = flag[2:].removeprefix("=")
        else:
            continue
        match = None if value.startswith("-") else _REPO_RE.fullmatch(value)
        if not match:
            return None, (
                f"Error: Invalid or missing repository '{value}' for --repo/-R. "
                "Expected OWNER/REPO or github.com/OWNER/REPO."
            )
        repo = match.group(1)
    return repo, None


# Native handlers that resolve a positional PR/issue reference.
_NATIVE_REF_COMMANDS = {
    ("pr", "merge"),
    ("pr", "status"),
    ("pr", "checks"),
    ("pr", "view"),
    ("issue", "view"),
}


# Native read handlers that implement no flags beyond ``--repo``/``-R``.
# ``run view`` also absorbs ``--log-failed``: native extraction already returns
# the failure block. ``--log`` needs the CLI to include successful-job logs.
_NATIVE_REPO_FLAG_ONLY = _NATIVE_REF_COMMANDS - {("pr", "merge")} | {("run", "view")}
_NATIVE_RUN_VIEW_LOG_FLAGS = frozenset({"--log-failed"})


def _has_unsupported_flags(args: list[str]) -> bool:
    """True when a native read command carries a flag its handler does not implement.

    The native handlers understand ``--repo``/``-R``.  ``run view`` also absorbs
    ``--log-failed`` (native extraction is the capped failure view).
    Any other flag (``--json``, ``--jq``, ``--job``, ...) was previously
    discarded, or read as a positional value (``pr checks --json`` became the
    commit SHA), so the caller got the native summary instead of what it asked
    for.  Such calls must reach the real gh CLI.
    """
    rest = args[2:]
    is_run_view = tuple(args[:2]) == ("run", "view")
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in ("--repo", "-R"):
            i += 2
            continue
        if token.startswith("--repo=") or (token.startswith("-R") and len(token) > 2):
            i += 1
            continue
        if is_run_view and token in _NATIVE_RUN_VIEW_LOG_FLAGS:
            i += 1
            continue
        if token.startswith("-"):
            return True
        i += 1
    return False


def _run_view_id(args: list[str]) -> str | None:
    """First positional token of ``run view``, skipping repo and log flags."""
    i = 2
    while i < len(args):
        token = args[i]
        if token in ("--repo", "-R"):
            i += 2
            continue
        if token.startswith("--repo=") or (token.startswith("-R") and len(token) > 2):
            i += 1
            continue
        if token in _NATIVE_RUN_VIEW_LOG_FLAGS:
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token
    return None


def _cli_reference_index(args: list[str]) -> int | None:
    """Find a positional target without mistaking read-option values for one."""
    value_flags = {
        "--repo",
        "-R",
        "--json",
        "--jq",
        "-q",
        "--template",
        "-t",
        "--interval",
        "-i",
        "--author",
        "--assignee",
    }
    rest = iter(range(2, len(args)))
    for index in rest:
        token = args[index]
        if token == "--":
            return next(rest, None)
        if token in value_flags:
            next(rest, None)
        elif not token.startswith("-"):
            return index
    return None


def _apply_repo_flag(args: list[str]) -> tuple[list[str], str | None]:
    """Strip ``--repo``/``-R`` from a native ref command and fold it into the ref.

    The handlers read the reference and extras (e.g. a commit SHA) by position,
    so a flag left in place was read as a positional value and a bare ``N``
    resolved against the working directory's repo instead of the named one.
    An explicit ``owner/repo#N`` or URL reference wins over the flag.
    """
    positional: list[str] = []
    repo_flags: list[str] = []
    rest = args[2:]
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in ("--repo", "-R"):
            repo_flags.extend(rest[i : i + 2])
            i += 2
            continue
        if token.startswith("--repo=") or (token.startswith("-R") and len(token) > 2):
            repo_flags.append(token)
        else:
            positional.append(token)
        i += 1
    repo, err = _parse_run_repo(repo_flags)
    if err:
        return args, err
    if positional:
        positional[0] = positional[0].strip()
    if repo and positional and re.fullmatch(r"#?\d+", positional[0]):
        positional[0] = f"{repo}#{positional[0].lstrip('#')}"
    return [*args[:2], *positional], None


def execute_gh(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute GitHub operations.

    Native handlers for high-value operations (issue view, pr view/status/checks/merge,
    run view).  Everything else passes through to the gh CLI unchanged.
    """
    # Structured ("tool"-format) callers supply the whole command as a single
    # ``command`` kwarg and leave ``args`` empty. Recover the positional args so
    # the native dispatch below works for both invocation styles (markdown
    # blocks set ``args`` positionally).
    if not args and kwargs and kwargs.get("command"):
        try:
            args = shlex.split(kwargs["command"])
        except ValueError as e:
            yield Message("system", f"Error parsing command: {e}")
            return
    # A markdown block may carry the command in its body instead of on the
    # fence line. Recover args from it too, so native handlers and their
    # argument guards (e.g. merge's unsupported-flag refusal) apply to every
    # invocation form instead of the body falling through to the raw CLI.
    if not args and code and code.strip():
        try:
            args = shlex.split(code.strip().removeprefix("gh "))
        except ValueError as e:
            yield Message("system", f"Error parsing command: {e}")
            return

    if (
        args
        and tuple(args[:2]) in _NATIVE_REPO_FLAG_ONLY
        and _has_unsupported_flags(args)
    ):
        # The CLI cannot see the separate structured-call URL. Preserve it as
        # the reference when the command has only flags, keeping an explicit
        # positional reference authoritative as in the native handlers.
        if tuple(args[:2]) in _NATIVE_REF_COMMANDS:
            ref_index = _cli_reference_index(args)
            if ref_index is None and kwargs and kwargs.get("url"):
                args = [*args[:2], kwargs["url"], *args[2:]]
                ref_index = 2
            if ref_index is not None:
                if args[:2] in (["pr", "status"], ["pr", "checks"]) and (
                    _cli_reference_index([*args[:2], *args[ref_index + 1 :]])
                    is not None
                ):
                    # CLI reads cannot select an arbitrary commit. Never drop
                    # the SHA and silently return checks for the current head.
                    yield Message(
                        "system",
                        "Error: A commit SHA cannot be combined with CLI read flags. "
                        "Remove the extra flags to use native commit-specific checks, "
                        "or use `gh api repos/OWNER/REPO/commits/SHA/check-runs`.",
                    )
                    return
                # Native status is PR-specific, but CLI status is a repository
                # overview with no positional target. Use view for flagged
                # PR-specific reads rather than dropping the requested target.
                if args[:2] == ["pr", "status"]:
                    args = ["pr", "view", *args[2:]]
                # owner/repo#N and #N are tool shorthand, not CLI references.
                ref = args[ref_index]
                qualified = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", ref)
                if qualified:
                    kind = "issues" if args[0] == "issue" else "pull"
                    ref = f"https://github.com/{qualified[1]}/{kind}/{qualified[2]}"
                elif re.fullmatch(r"#\d+", ref):
                    ref = ref[1:]
                args = [*args[:ref_index], ref, *args[ref_index + 1 :]]
        yield from _passthrough_gh(args, code)
        return

    if args and tuple(args[:2]) in _NATIVE_REF_COMMANDS:
        args, repo_err = _apply_repo_flag(args)
        if repo_err:
            yield Message("system", repo_err)
            return

    if args and len(args) >= 2 and args[0] == "pr" and args[1] == "merge":
        yield from _handle_pr_merge(args, kwargs)

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "status":
        yield from _handle_pr_status(args, kwargs)

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "checks":
        info, err = _resolve_ref(args, kwargs, "pull", "PR reference")
        if err:
            yield err
            return

        assert info is not None
        commit_sha = args[3] if args and len(args) > 3 else None
        url = f"https://github.com/{info['owner']}/{info['repo']}/pull/{info['number']}"
        yield from _wait_for_checks(
            info["owner"], info["repo"], url, commit_sha=commit_sha
        )

    elif args and len(args) >= 2 and args[0] == "pr" and args[1] == "view":
        info, err = _resolve_ref(args, kwargs, "pull", "PR reference")
        if err:
            yield err
            return

        assert info is not None
        if info["type"] != "pull":
            yield Message(
                "system",
                f"Error: Reference is not a GitHub PR (got {info['type']}). Use `gh issue view` for issues.",
            )
            return

        url = f"https://github.com/{info['owner']}/{info['repo']}/pull/{info['number']}"
        content = get_github_pr_content(url)
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                "Error: Failed to fetch PR content. Make sure 'gh' CLI is installed and authenticated.",
            )

    elif args and len(args) >= 2 and args[0] == "issue" and args[1] == "view":
        info, err = _resolve_ref(args, kwargs, "issues", "issue reference")
        if err:
            yield err
            return

        assert info is not None
        if info["type"] == "pull":
            yield Message(
                "system",
                f"Error: Reference is not a GitHub issue (got {info['type']}). Use `gh pr view` for pull requests.",
            )
            return

        content = get_github_issue_content(info["owner"], info["repo"], info["number"])
        if content:
            yield Message("system", content)
        else:
            yield Message(
                "system",
                "Error: Failed to fetch issue content. Make sure 'gh' CLI is installed and authenticated.",
            )

    elif args and len(args) >= 2 and args[0] == "run" and args[1] == "view":
        run_id = _run_view_id(args)
        if not run_id:
            yield Message(
                "system", "Error: No run ID provided. Usage: gh run view <run-id>"
            )
            return

        if not run_id.isdigit():
            yield Message(
                "system",
                f"Error: Invalid run ID '{run_id}'. Run IDs are numeric (e.g. 12345678).",
            )
            return

        repo, repo_err = _parse_run_repo(args[2:])
        if repo_err:
            yield Message("system", repo_err)
            return

        content = (
            get_github_run_logs(run_id, repo=repo)
            if repo
            else get_github_run_logs(run_id)
        )
        if content:
            # Same credential-redaction policy as the pass-through path: a
            # failed job log can echo tokens (e.g. GITHUB_TOKEN=ghp_...).
            yield Message("system", _redact_passthrough_output(content))
        else:
            yield Message(
                "system",
                f"Error: Failed to fetch run {run_id}. Make sure 'gh' CLI is installed and authenticated.",
            )

    else:
        # Pass through to gh CLI for all other commands
        yield from _passthrough_gh(args, code)


instructions = """### When to use

Use `gh` for GitHub issues, PRs, CI runs, and merges.
Refs: URLs, `owner/repo#N`, `#N`, or bare `N`.

Native commands:
- `gh issue view <ref>`: body and comments
- `gh pr view <ref>`: body, comments, review threads, CI, mergeability
- `gh pr status <ref> [commit_sha]`: CI state and run IDs
- `gh pr checks <ref> [commit_sha]`: waits for failure, completion, or 60s
- `gh pr merge <ref> ...`: squash default, optional head-commit protection
- `gh run view <run-id>`: failed-job logs

Read flags beyond `--repo`/`-R` and other subcommands use the CLI, except
`run view --log-failed` stays native (capped failure extraction).
A separate `url` supplies the target. Flagged PR-specific `pr status` uses
`pr view` (CLI `pr status` has no target).

Structured calls pass the whole command as `command`, e.g.
`{"command": "issue view owner/repo#42"}`."""


def examples(tool_format):
    def render(args: list[str]) -> str:
        # Native ("tool") calls carry a single `command` string parameter;
        # markdown/XML carry the command positionally.
        if tool_format == "tool":
            return ToolUse("gh", None, None, {"command": shlex.join(args)}).to_output(
                tool_format
            )
        return ToolUse("gh", args, None).to_output(tool_format)

    return f"""
> User: read PR #123 on owner/repo
> Assistant:
{render(["pr", "view", "owner/repo#123"])}

> User: check CI status for this PR
> Assistant:
{render(["pr", "status", "https://github.com/owner/repo/pull/123"])}
> System:
PR #123 checks (abc1234):
Total: 6 checks
✅ 4 passed
❌ 2 failed

Failed runs:
  - build (run 12345678)
  - test (run 12345679)

View logs: gh run view <run_id> --log-failed

> User: show me the failed build logs
> Assistant:
{render(["run", "view", "12345678"])}

> User: wait for CI checks to complete on a PR
> Assistant:
{render(["pr", "checks", "https://github.com/owner/repo/pull/123"])}

> User: merge PR #123 on owner/repo
> Assistant:
{render(["pr", "merge", "owner/repo#123"])}

> User: auto-merge PR when checks pass, and delete the branch
> Assistant:
{render(["pr", "merge", "owner/repo#123", "--squash", "--auto", "--delete-branch"])}

> User: read issue #42 on owner/repo
> Assistant:
{render(["issue", "view", "owner/repo#42"])}

> User: show issues (pass-through to gh CLI)
> Assistant:
{render(["issue", "list", "--repo", "owner/repo"])}

> User: post a multi-line comment on issue 42
> Assistant:
{
        ToolUse(
            "shell",
            [],
            '''gh issue comment 42 --repo owner/repo --body-file - << 'EOF'
## Summary

Work is complete. Here are the details:
- Fixed the bug
- Added tests

See PR #123 for the implementation.
EOF''',
        ).to_output(tool_format)
    }
"""


tool: ToolSpec = ToolSpec(
    name="gh",
    available=has_gh_tool(),
    desc="Interact with GitHub",
    instructions=instructions,
    examples=examples,
    execute=execute_gh,
    block_types=["gh"],
    parameters=[
        Parameter(
            name="command",
            type="string",
            description=(
                "The gh command and its arguments, e.g. "
                "'issue view owner/repo#42' or 'pr checks owner/repo#5'."
            ),
            required=True,
        ),
        Parameter(
            name="url",
            type="string",
            description=(
                "A GitHub URL to the PR or issue, as an alternative to embedding "
                "it inside command (e.g. 'https://github.com/owner/repo/pull/42'). "
                "Used only by 'pr view/status/checks/merge' and 'issue view'; "
                "other commands ignore it, so include the target in command."
            ),
            required=False,
        ),
    ],
)
