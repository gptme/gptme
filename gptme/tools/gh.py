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

from ..message import Message
from ..util.gh import (
    DEFAULT_LOG_MAX_TOKENS,
    _truncate_log_tail,
    get_github_issue_content,
    get_github_pr_content,
    get_github_run_logs,
    merge_github_pr,
    parse_github_ref,
    parse_github_url,
)
from . import Parameter, ToolSpec, ToolUse

logger = logging.getLogger(__name__)


def has_gh_tool() -> bool:
    return shutil.which("gh") is not None


def _get_commit_check_runs(owner: str, repo: str, sha: str) -> list[dict]:
    """Collect a complete, consistent check-run listing or raise on partial data."""
    runs: list[dict] = []
    seen: set[int] = set()
    total: int | None = None
    page = 1
    while True:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"/repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100&page={page}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("Invalid check-run response")
        count = data.get("total_count")
        batch = data.get("check_runs")
        if type(count) is not int or count < 0 or not isinstance(batch, list):
            raise ValueError("Missing or invalid check-run count/list")
        if total is None:
            total = count
        if count != total:
            raise ValueError("Check-run total changed during pagination; retry")
        for run in batch:
            if not isinstance(run, dict) or type(run.get("id")) is not int:
                raise ValueError("Invalid check-run entry")
            if run["id"] in seen:
                raise ValueError("Repeated check run during pagination; retry")
            seen.add(run["id"])
            runs.append(run)
        if len(runs) > total:
            raise ValueError("Check-run listing exceeds reported total")
        if len(runs) == total:
            return runs
        if not batch:
            raise ValueError("Incomplete check-run listing: empty page before total")
        page += 1


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

    logger.info("gh pass-through: %s", shlex.join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False
        )
        max_chars = DEFAULT_LOG_MAX_TOKENS * 4
        output = _truncate_log_tail((result.stdout or "").strip(), max_chars)
        stderr = _truncate_log_tail((result.stderr or "").strip(), max_chars)
        if result.returncode != 0:
            details = []
            if stderr:
                details.append(f"stderr:\n{stderr}")
            if output:
                details.append(f"stdout:\n{output}")
            msg = f"Error (exit {result.returncode})"
            if details:
                msg += "\n\n" + "\n\n".join(details)
            yield Message("system", msg)
        elif len((result.stdout or "").strip()) > max_chars:
            yield Message(
                "system",
                "Command succeeded (exit 0), but stdout exceeded the response limit "
                "and was omitted. Do not repeat a mutating command: its changes have "
                "already been applied. Inspect the result with a read-only query "
                "using --jq/--limit, or use the shell tool to redirect a read-only "
                "query to a file.",
            )
        elif output:
            yield Message("system", output)
        else:
            yield Message("system", "(no output)")
    except subprocess.TimeoutExpired:
        yield Message("system", "Error: Command timed out after 60 seconds")
    except FileNotFoundError:
        yield Message(
            "system",
            "Error: gh CLI not found. Install from https://cli.github.com/",
        )


_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


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
        if value.startswith("-") or not _REPO_RE.fullmatch(value):
            return None, (
                f"Error: Invalid or missing repository '{value}' for --repo/-R. "
                "Expected OWNER/REPO."
            )
        repo = value
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
        info, err = _resolve_ref(args, kwargs, "issue", "issue reference")
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
            yield Message("system", content)
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
