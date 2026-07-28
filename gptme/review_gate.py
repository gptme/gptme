"""Review-gated delivery enforcement for autonomous workflows.

Validates that a git checkout is eligible for review-gated delivery:
  - The checkout is inside a git repository with at least one commit.
  - A remote with a push URL is reachable (so a branch can be pushed).
  - The current branch is NOT the repository default branch.

If any of these conditions are not met, :func:`validate` raises
:exc:`ReviewGateError` so the caller fails closed rather than delivering
changes silently to the wrong target.

This is the enforcement layer for the pattern described in
``docs/automation.rst`` under "Review-Gated Autonomous Workflows".

Usage::

    from gptme.review_gate import validate, ReviewGateError

    try:
        evidence = validate(path=Path("."), remote="origin")
    except ReviewGateError as exc:
        print(f"Cannot deliver: {exc}")
        sys.exit(1)

    # Inspect evidence before pushing
    print(evidence.diff_stat)
    print(evidence.branch)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .util.git_cmd import GIT_CMD


class ReviewGateError(Exception):
    """Raised when the review gate cannot be established.

    All callers should treat this as a hard stop — do not catch and continue,
    as that would bypass the safety boundary this module is designed to enforce.
    """


@dataclass(frozen=True)
class DeliveryEvidence:
    """Evidence collected for the PR description and human review."""

    branch: str
    base_ref: str
    remote: str
    commits: str
    diff_stat: str
    changed_files: list[str]


# ── Internal helpers ──────────────────────────────────────────────────────


def _run(args: list[str], cwd: Path, timeout: int = 15) -> str:
    """Run a git sub-command and return stdout, or raise on failure."""
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ReviewGateError(
            f"git command failed: {' '.join(args[1:])!r}\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _is_git_repo(path: Path) -> bool:
    result = subprocess.run(
        [GIT_CMD, "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _get_current_branch(path: Path) -> str:
    result = subprocess.run(
        [GIT_CMD, "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise ReviewGateError("Cannot determine current branch (no commits yet?)")
    branch = result.stdout.strip()
    if branch == "HEAD":
        raise ReviewGateError(
            "Repository is in detached HEAD state. "
            "Check out a named feature branch before delivering."
        )
    return branch


def _get_default_branch(path: Path, remote: str) -> str:
    """Return the default branch name for *remote*, e.g. 'master' or 'main'."""
    # Try the symbolic-ref the remote advertises (works after fetch --tags).
    result = subprocess.run(
        [GIT_CMD, "rev-parse", "--abbrev-ref", f"{remote}/HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode == 0:
        ref = result.stdout.strip()
        # ref is like "origin/master" → strip the remote prefix
        prefix = f"{remote}/"
        if ref.startswith(prefix):
            return ref[len(prefix):]

    # Fallback: ask the remote directly (network call; may be slow or fail).
    result = subprocess.run(
        [GIT_CMD, "remote", "show", remote],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("HEAD branch:"):
                return line.split(":", 1)[1].strip()

    # Last resort: check whether common default names exist on the remote.
    for candidate in ("master", "main"):
        r = subprocess.run(
            [GIT_CMD, "rev-parse", "--verify", f"{remote}/{candidate}"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if r.returncode == 0:
            return candidate

    raise ReviewGateError(
        f"Cannot determine the default branch for remote '{remote}'. "
        "Run 'git fetch' and retry."
    )


def _has_remote(path: Path, remote: str) -> bool:
    result = subprocess.run(
        [GIT_CMD, "remote", "get-url", remote],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _collect_evidence(path: Path, remote: str, base_ref: str, branch: str) -> DeliveryEvidence:
    """Collect diff and commit evidence relative to *base_ref*."""
    range_spec = f"{remote}/{base_ref}...HEAD"

    commits = subprocess.run(
        [GIT_CMD, "log", "--oneline", range_spec],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    ).stdout.strip()

    diff_stat = subprocess.run(
        [GIT_CMD, "diff", "--stat", range_spec],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    ).stdout.strip()

    files_out = subprocess.run(
        [GIT_CMD, "diff", "--name-only", range_spec],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    ).stdout.strip()

    changed_files = [f for f in files_out.splitlines() if f]

    return DeliveryEvidence(
        branch=branch,
        base_ref=base_ref,
        remote=remote,
        commits=commits,
        diff_stat=diff_stat,
        changed_files=changed_files,
    )


# ── Public API ────────────────────────────────────────────────────────────


def validate(
    path: Path | str = ".",
    remote: str = "origin",
) -> DeliveryEvidence:
    """Validate that delivery can proceed through the review gate.

    Checks (in order):

    1. *path* is inside a git working tree.
    2. The remote *remote* is configured with a push URL.
    3. The current branch is NOT the repository's default branch.

    Returns a :class:`DeliveryEvidence` snapshot on success.

    Raises :exc:`ReviewGateError` if any check fails.  All failures are
    intended to be hard stops — callers should not catch the exception and
    continue, as that would bypass the safety boundary.

    Args:
        path: Directory to inspect (defaults to the current working directory).
        remote: Remote name to validate against (defaults to ``"origin"``).
    """
    cwd = Path(path).resolve()

    # 1. Must be inside a git repository.
    if not _is_git_repo(cwd):
        raise ReviewGateError(
            f"'{cwd}' is not inside a git repository. "
            "Review-gated delivery requires version control. "
            "No review-gated delivery guarantee is active."
        )

    # 2. Remote must be configured.
    if not _has_remote(cwd, remote):
        raise ReviewGateError(
            f"Remote '{remote}' is not configured. "
            "Review-gated delivery requires a remote so changes can be pushed "
            "to a branch for PR review. "
            "No review-gated delivery guarantee is active."
        )

    # 3. Current branch must not be the default branch.
    branch = _get_current_branch(cwd)
    default = _get_default_branch(cwd, remote)

    if branch == default:
        raise ReviewGateError(
            f"Current branch '{branch}' is the default branch. "
            "Pushing directly to the default branch bypasses PR review. "
            "Create a feature branch and work from there: "
            f"  git checkout -b feat/my-task --no-track {remote}/{default}"
        )

    evidence = _collect_evidence(cwd, remote, default, branch)
    return evidence
