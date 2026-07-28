"""Review-gated delivery enforcement for autonomous workflows.

Validates that a target branch is eligible for review-gated push: not the
default branch, and a VCS remote is reachable. Call ``check_delivery_target``
before pushing from any autonomous workflow.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class ReviewGateStatus(Enum):
    OK = "ok"
    NO_REMOTE = "no_remote"
    DEFAULT_BRANCH = "default_branch"


@dataclass
class ReviewGateResult:
    status: ReviewGateStatus
    message: str

    @property
    def ok(self) -> bool:
        return self.status == ReviewGateStatus.OK


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout.strip()


def get_remote_names(repo: Path) -> list[str]:
    """Return the configured git remote names for the repo."""
    rc, out = _run_git(["remote"], repo)
    if rc != 0 or not out:
        return []
    return out.splitlines()


def get_default_branch(repo: Path, remote: str = "origin") -> str | None:
    """Return the default branch name for the remote, or None if it cannot be determined."""
    # Prefer the symbolic ref set by 'git fetch'/'git remote set-head'
    rc, out = _run_git(["symbolic-ref", f"refs/remotes/{remote}/HEAD"], repo)
    if rc == 0 and out:
        return out.split("/")[-1]

    # Fall back to 'git remote show' (may make a network call)
    rc, out = _run_git(["remote", "show", remote], repo)
    if rc == 0:
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("HEAD branch:"):
                return stripped.split(":", 1)[1].strip()

    # Final heuristic: check well-known default branch names
    for name in ("master", "main"):
        rc, _ = _run_git(
            ["rev-parse", "--verify", f"refs/remotes/{remote}/{name}"], repo
        )
        if rc == 0:
            return name

    return None


def check_delivery_target(
    branch: str,
    repo: Path,
    remote: str = "origin",
) -> ReviewGateResult:
    """Validate that *branch* is eligible for review-gated push to *remote*.

    Returns:
        ReviewGateResult with status OK, NO_REMOTE, or DEFAULT_BRANCH.
    """
    remotes = get_remote_names(repo)
    if remote not in remotes:
        return ReviewGateResult(
            status=ReviewGateStatus.NO_REMOTE,
            message=(
                f"No remote '{remote}' configured. "
                "Review-gated delivery requires a VCS remote."
            ),
        )

    default = get_default_branch(repo, remote)
    if default is not None and branch == default:
        return ReviewGateResult(
            status=ReviewGateStatus.DEFAULT_BRANCH,
            message=(
                f"Branch '{branch}' is the default branch. "
                "Review-gated delivery requires a feature branch, not the default branch."
            ),
        )

    return ReviewGateResult(
        status=ReviewGateStatus.OK,
        message=(
            f"Branch '{branch}' is eligible for review-gated delivery "
            f"via remote '{remote}'."
        ),
    )
