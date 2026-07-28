"""Review-gated delivery enforcement for autonomous workflows.

Validates that a target branch is eligible for review-gated push: not the
default branch, and a VCS remote is reachable. Call ``check_delivery_target``
before pushing from any autonomous workflow.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 10
_REVIEW_GATE_ENV = "GPTME_REVIEW_GATE"


class ReviewGateStatus(Enum):
    OK = "ok"
    NO_REMOTE = "no_remote"
    DEFAULT_BRANCH = "default_branch"
    AMBIGUOUS_TARGET = "ambiguous_target"


@dataclass
class ReviewGateResult:
    status: ReviewGateStatus
    message: str

    @property
    def ok(self) -> bool:
        return self.status == ReviewGateStatus.OK


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
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
        prefix = f"refs/remotes/{remote}/"
        if out.startswith(prefix):
            return out[len(prefix) :]

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
    if default is None:
        return ReviewGateResult(
            status=ReviewGateStatus.NO_REMOTE,
            message=(
                f"Cannot determine the default branch for remote '{remote}'. "
                "Review-gated delivery requires an established review boundary."
            ),
        )

    if branch == default:
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


def _command_words(node: object) -> list[str] | None:
    """Return command words, marking dynamically expanded words as unknown."""
    if getattr(node, "kind", None) != "command":
        return None

    words: list[str] = []
    for part in getattr(node, "parts", []):
        kind = getattr(part, "kind", None)
        if kind == "redirect":
            continue
        if kind == "assignment" and not words:
            continue
        if kind != "word":
            return None
        words.append("" if getattr(part, "parts", []) else part.word)
    return words


def _walk_shell_nodes(node: object) -> list[object]:
    """Return *node* and all nested bashlex nodes without following cycles."""
    nodes: list[object] = []
    stack = [node]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        nodes.append(current)
        for value in vars(current).values():
            if hasattr(value, "kind"):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if hasattr(item, "kind"))
    return nodes


def _git_push_arguments(words: list[str]) -> list[str] | None:
    """Extract arguments after a literal ``git push`` command."""
    try:
        git_index = words.index("git")
        push_index = words.index("push", git_index + 1)
    except ValueError:
        return None

    # Wrapper commands are allowed only when they directly execute git. This supports
    # common ``env``, ``command``, and ``sudo`` forms without mistaking
    # ``echo git push ...`` for delivery.
    prefix = words[:git_index]
    if prefix and prefix[0] not in {"bg", "command", "env", "sudo"}:
        return None
    return words[push_index + 1 :]


def _git_push_targets(command: str) -> list[tuple[str, str | None]]:
    """Return literal ``(remote, destination)`` pairs from a shell command.

    The parser intentionally returns an unknown destination for any push it cannot
    establish statically. Review-gated mode then fails closed instead of guessing.
    """
    import bashlex

    try:
        roots = bashlex.parse(command)
    except Exception:
        # A lexical push signal in an unparseable command is ambiguous and blocked.
        return [("origin", None)] if "git" in command and "push" in command else []

    targets: list[tuple[str, str | None]] = []
    dynamic_commands = {".", "bash", "eval", "sh", "source", "xargs"}
    for root in roots:
        for node in _walk_shell_nodes(root):
            if getattr(node, "kind", None) != "command":
                continue
            words = _command_words(node)
            if words is None:
                targets.append(("origin", None))
                continue
            args = _git_push_arguments(words)
            if args is None:
                # Dynamic command loaders/evaluators can hide arbitrary pushes from
                # static inspection, so review-gated mode must reject them.
                if words and words[0] in dynamic_commands:
                    targets.append(("origin", None))
                # A variable-expanded command word (empty string from _command_words)
                # before a literal 'push' cannot be statically verified as non-git;
                # e.g. `$g push origin master` bypasses the literal 'git' check.
                elif words and words[0] == "" and "push" in words:
                    targets.append(("origin", None))
                continue

            positional: list[str] = []
            option_remote: str | None = None
            skip_next = False
            for index, arg in enumerate(args):
                if skip_next:
                    skip_next = False
                    continue
                if arg == "--repo" and index + 1 < len(args):
                    option_remote = args[index + 1]
                    skip_next = True
                elif arg.startswith("--repo="):
                    option_remote = arg.split("=", 1)[1]
                elif arg in {"--receive-pack", "--exec", "-o", "--push-option"}:
                    skip_next = True
                elif not arg.startswith("-"):
                    positional.append(arg)

            remote = option_remote or (positional[0] if positional else "origin")
            refspecs = positional if option_remote else positional[1:]
            if not refspecs:
                targets.append((remote, None))
                continue

            for refspec in refspecs:
                literal_destination = refspec.rsplit(":", 1)[-1].removeprefix(
                    "refs/heads/"
                )
                destination: str | None = literal_destination
                if literal_destination.startswith("+") or any(
                    char in literal_destination for char in "$`*?[{"
                ):
                    destination = None
                targets.append((remote, destination))
    return targets


def check_shell_delivery(
    command: str, repo: Path | None = None
) -> ReviewGateResult | None:
    """Validate every git push in *command* when review-gated mode is active.

    Returns ``None`` outside review-gated mode or when the command has no push.
    Ambiguous pushes fail closed because their destination cannot be reviewed before
    execution.
    """
    if os.environ.get(_REVIEW_GATE_ENV, "").lower() not in {"1", "true", "yes", "on"}:
        return None

    targets = _git_push_targets(command)
    if not targets:
        return None

    repo = repo or Path.cwd()
    for remote, branch in targets:
        if (
            not branch
            or branch in {"HEAD", "@{push}"}
            or branch.startswith(("+", "refs/tags/"))
        ):
            return ReviewGateResult(
                status=ReviewGateStatus.AMBIGUOUS_TARGET,
                message=(
                    "Review-gated delivery requires an explicit literal destination "
                    "branch: use 'git push <remote> "
                    "HEAD:refs/heads/<feature-branch>'."
                ),
            )
        result = check_delivery_target(branch, repo, remote)
        if not result.ok:
            return result

    return ReviewGateResult(
        status=ReviewGateStatus.OK,
        message="All git push destinations passed the review gate.",
    )
