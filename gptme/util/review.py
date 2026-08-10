"""Shared data types for the gptme review pipeline (gptme#3442).

The review pipeline is a two-stage autonomous loop:

    pr_review (gptme-contrib)     →   review-watch (gptme)
    ─────────────────────────         ─────────────────────
    AI reviews PR diff                AI acts as author
    Emits structured findings         Reads comments → fixes
    ReviewArtifact produced           ReviewArtifact consumed (optional)

``ReviewArtifact`` is the structured JSON handoff between the two stages.
When ``pr_review`` produces one, ``review-watch`` can consume it for
richer context (exact file/line, severity, confirmed/dropped status).

All fields are optional so the artifact degrades gracefully to the
plain-text comment path when not available.

What this module is for
-----------------------
This is the **canonical schema** for review findings and artifacts, and it
backs the model-facing primitive ``gptme-util review pr`` (diff in,
``ReviewArtifact`` out). Downstream reviewers are expected to emit *this*
schema rather than define their own.

What this module is NOT for
---------------------------
It deliberately contains **no forge plumbing** — no GitHub/Forgejo API calls,
no comment posting, no idempotency markers. ``review pr`` writes nothing; it
produces an artifact and exits. Publishing findings to a forge is out of scope
for gptme core and belongs in a downstream adapter.

Related implementations
-----------------------
Three PR-review implementations exist across three repos, and it is easy to
land in the wrong one:

- **this module + ``gptme/cli/cmd_review_pr.py``** (gptme core, public) — the
  schema and the diff→findings primitive. No forge writes, by design.
- **``gptme_runloops.pr_review``** (gptme-contrib) — forge-neutral local-diff
  entry point (``review --working-tree``): review uncommitted work with no
  forge calls at all.
- **Bob's ``scripts/github/ai-review.py``** (private brain repo) — the only
  one that posts findings to GitHub in production (marker + inline comments,
  consensus filtering, suppression ledger).

Which one do you want?

- Reviewing a PR diff, or building something that consumes findings: use
  ``gptme-util review pr`` and this module's schema.
- Reviewing uncommitted work in your own checkout, no forge involved: use
  ``review --working-tree`` from gptme-contrib.
- Publishing findings back to a forge as review comments: not in gptme core.
  Consume a ``ReviewArtifact`` from either of the above and post it from your
  own adapter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path


class FindingSeverity(str, Enum):
    """Severity levels for review findings, ordered low → high."""

    NOTE = "note"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    """Lifecycle status of a finding during the review-watch fix loop."""

    OPEN = "open"
    """Finding has not been addressed yet."""
    CONFIRMED = "confirmed"
    """Reviewer confirmed the fix is correct."""
    DROPPED = "dropped"
    """Finding was intentionally not acted on (e.g. won't-fix, out-of-scope)."""
    IN_PROGRESS = "in_progress"
    """A fix session is currently working on this finding."""


@dataclass
class ReviewFinding:
    """A single review finding produced by an AI reviewer.

    Corresponds to one inline PR comment or review note.  ``file`` and
    ``line`` mirror the GitHub PR review comment fields so the data can be
    round-tripped to/from the API without loss.
    """

    #: Short description of the finding (matches the reviewer comment body
    #: when the artifact is produced from existing review comments).
    body: str

    #: File path relative to repo root, or empty string for PR-level findings.
    file: str = ""

    #: Line number (1-based) in the file, or ``None`` for file-level comments.
    line: int | None = None

    #: Severity of the finding.
    severity: FindingSeverity = FindingSeverity.WARNING

    #: Current lifecycle status.
    status: FindingStatus = FindingStatus.OPEN

    #: GitHub review comment ID, if produced from an API comment.
    github_comment_id: int | None = None

    #: Optional reviewer login for attribution.
    reviewer: str = ""

    def to_dict(self) -> dict:
        return {
            "body": self.body,
            "file": self.file,
            "line": self.line,
            "severity": self.severity.value,
            "status": self.status.value,
            "github_comment_id": self.github_comment_id,
            "reviewer": self.reviewer,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ReviewFinding:
        if not isinstance(d, dict):
            raise TypeError(
                f"ReviewFinding.from_dict expects a dict, got {type(d).__name__!r}"
            )
        file_raw = d.get("file", "")
        if not isinstance(file_raw, str):
            raise ValueError(
                f"ReviewFinding.file must be a string, got {type(file_raw).__name__!r}: {file_raw!r}"
            )
        line_raw = d.get("line")
        if line_raw is not None and type(line_raw) is not int:
            raise ValueError(
                f"ReviewFinding.line must be an int or None, got {type(line_raw).__name__!r}: {line_raw!r}"
            )
        try:
            severity = FindingSeverity(d.get("severity", FindingSeverity.WARNING.value))
        except (TypeError, ValueError):
            severity = FindingSeverity.WARNING
        try:
            status = FindingStatus(d.get("status", FindingStatus.OPEN.value))
        except (TypeError, ValueError):
            status = FindingStatus.OPEN
        body_raw = d.get("body", "")
        if not isinstance(body_raw, str):
            raise ValueError(
                f"ReviewFinding.body must be a string, got {type(body_raw).__name__!r}: {body_raw!r}"
            )
        return cls(
            body=body_raw,
            file=file_raw,
            line=line_raw,
            severity=severity,
            status=status,
            github_comment_id=d.get("github_comment_id"),
            reviewer=d.get("reviewer", ""),
        )

    @classmethod
    def from_github_comment(
        cls,
        comment: dict,
        *,
        severity: FindingSeverity = FindingSeverity.WARNING,
    ) -> ReviewFinding:
        """Construct a finding from a raw GitHub PR review comment dict."""
        return cls(
            body=comment.get("body", "").strip(),
            file=comment.get("path", ""),
            line=comment.get("original_line") or comment.get("line"),
            severity=severity,
            status=FindingStatus.OPEN,
            github_comment_id=comment.get("id"),
            reviewer=comment.get("user", {}).get("login", ""),
        )


class ReviewStatus(str, Enum):
    """Status of a review pass indicating whether it completed fully."""

    COMPLETE = "complete"
    """Review completed successfully and all findings were extracted cleanly."""
    INCOMPLETE = "incomplete"
    """Review session failed or timed out, but some findings were extracted from partial output."""


@dataclass
class ReviewArtifact:
    """Structured output of a review pass, consumed by review-watch.

    Produced by ``pr_review`` (gptme-contrib) and optionally consumed by
    ``review-watch`` to give the fix session exact coordinates (file, line,
    severity) instead of raw comment text.

    The artifact can be serialised to/from JSON for disk persistence and
    inter-process handoff.

    Example JSON schema::

        {
          "schema_version": 2,
          "pr": {"owner": "gptme", "repo": "gptme", "number": 1234},
          "review_status": "complete",
          "findings": [
            {
              "body": "This variable name is unclear.",
              "file": "gptme/util/review.py",
              "line": 42,
              "severity": "warning",
              "status": "open",
              "github_comment_id": 99887766,
              "reviewer": "ErikBjare"
            }
          ]
        }
    """

    #: The PR this artifact was produced for.
    pr_owner: str
    pr_repo: str
    pr_number: int

    #: Findings from the review pass.
    findings: list[ReviewFinding] = field(default_factory=list)

    #: Whether the review completed fully or was interrupted.
    review_status: ReviewStatus = ReviewStatus.COMPLETE

    #: Exit reason from the reviewer session ("done", "error", "timeout").
    session_exit_reason: str = ""

    #: Error message from the reviewer session, if any.
    session_error: str = ""

    #: Duration of the review session in seconds.
    review_duration_s: float = 0.0

    #: Number of findings entries that were skipped due to validation errors.
    validation_errors: int = 0

    #: Schema version for forward-compatibility.
    schema_version: Literal[2] = 2

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------

    @property
    def open_findings(self) -> list[ReviewFinding]:
        """Return only findings that have not yet been addressed."""
        return [f for f in self.findings if f.status == FindingStatus.OPEN]

    @property
    def confirmed_count(self) -> int:
        return sum(1 for f in self.findings if f.status == FindingStatus.CONFIRMED)

    @property
    def dropped_count(self) -> int:
        return sum(1 for f in self.findings if f.status == FindingStatus.DROPPED)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "pr": {
                "owner": self.pr_owner,
                "repo": self.pr_repo,
                "number": self.pr_number,
            },
            "review_status": self.review_status.value,
            "session_exit_reason": self.session_exit_reason,
            "session_error": self.session_error,
            "review_duration_s": self.review_duration_s,
            "validation_errors": self.validation_errors,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: Path) -> None:
        """Persist the artifact to a JSON file."""
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> ReviewArtifact:
        if not isinstance(d, dict):
            raise TypeError(
                f"ReviewArtifact.from_dict expects a dict, got {type(d).__name__!r}"
            )
        pr = d.get("pr", {})
        if not isinstance(pr, dict):
            pr = {}
        review_status_raw = d.get("review_status", ReviewStatus.COMPLETE.value)
        try:
            review_status = ReviewStatus(review_status_raw)
        except (TypeError, ValueError):
            # Fail-safe: an unrecognised or container-typed status value is treated as
            # INCOMPLETE rather than COMPLETE so that the review-watch guard is not
            # silently bypassed when an artifact was produced by a newer tool version
            # with new status values, or when the field holds a malformed non-string value.
            review_status = ReviewStatus.INCOMPLETE

        # Deserialise findings one-by-one so a malformed entry does not crash the
        # whole load.  Each validation error downgrades the artifact to INCOMPLETE.
        deserialization_errors = 0
        try:
            stored_validation_errors = int(d.get("validation_errors", 0))
        except (TypeError, ValueError):
            stored_validation_errors = 0
            deserialization_errors += 1
        findings: list[ReviewFinding] = []
        findings_raw = d.get("findings", [])
        if not isinstance(findings_raw, list):
            # Non-list findings container (null, int, dict …): treat as a deserialization
            # error so the artifact is marked INCOMPLETE instead of crashing.
            findings_raw = []
            deserialization_errors += 1
        for f_raw in findings_raw:
            try:
                findings.append(ReviewFinding.from_dict(f_raw))
            except (ValueError, TypeError, KeyError, AttributeError):
                deserialization_errors += 1

        if deserialization_errors > 0 and review_status == ReviewStatus.COMPLETE:
            review_status = ReviewStatus.INCOMPLETE

        # Coerce numeric and string metadata fields — guard against container values
        # (list, dict …) that would otherwise be stored as-is and cause type errors
        # downstream when the artifact is used (e.g., string formatting, logging).
        pr_number_raw = pr.get("number", 0)
        if isinstance(pr_number_raw, bool):
            pr_number_val = 0
            deserialization_errors += 1
        else:
            try:
                pr_number_val = int(pr_number_raw)
            except (TypeError, ValueError):
                pr_number_val = 0
                deserialization_errors += 1

        try:
            review_duration_s = float(d.get("review_duration_s", 0.0))
        except (TypeError, ValueError):
            review_duration_s = 0.0
            deserialization_errors += 1

        # Coerce pr.owner and pr.repo to strings — if they're containers
        # (lists, dicts), set them to empty string and count as an error.
        pr_owner_val = pr.get("owner", "")
        if not isinstance(pr_owner_val, str):
            pr_owner_val = ""
            deserialization_errors += 1

        pr_repo_val = pr.get("repo", "")
        if not isinstance(pr_repo_val, str):
            pr_repo_val = ""
            deserialization_errors += 1

        # Re-evaluate status after coercion errors — a malformed pr.owner,
        # pr.repo, pr.number or review_duration_s still counts as a deserialization problem.
        if deserialization_errors > 0 and review_status == ReviewStatus.COMPLETE:
            review_status = ReviewStatus.INCOMPLETE

        return cls(
            pr_owner=pr_owner_val,
            pr_repo=pr_repo_val,
            pr_number=pr_number_val,
            findings=findings,
            review_status=review_status,
            session_exit_reason=d.get("session_exit_reason", ""),
            session_error=d.get("session_error", ""),
            review_duration_s=review_duration_s,
            validation_errors=stored_validation_errors + deserialization_errors,
        )

    @classmethod
    def from_json(cls, text: str) -> ReviewArtifact:
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path) -> ReviewArtifact:
        """Load an artifact from a JSON file."""
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def from_github_comments(
        cls,
        *,
        owner: str,
        repo: str,
        pr_number: int,
        inline_comments: list[dict],
        conversation_comments: list[dict],
    ) -> ReviewArtifact:
        """Build an artifact from raw GitHub API comment dicts.

        Inline PR review comments are treated as ``WARNING`` severity by
        default.  Conversation-level comments (PR thread, not inline) use
        ``NOTE`` severity to reflect their lower specificity.
        """
        findings: list[ReviewFinding] = [
            ReviewFinding.from_github_comment(c, severity=FindingSeverity.WARNING)
            for c in inline_comments
        ]
        findings.extend(
            ReviewFinding.from_github_comment(c, severity=FindingSeverity.NOTE)
            for c in conversation_comments
        )
        return cls(
            pr_owner=owner,
            pr_repo=repo,
            pr_number=pr_number,
            findings=findings,
        )
