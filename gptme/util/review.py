"""Shared data types for the gptme review pipeline (gptme#3442).

The review pipeline is a two-stage autonomous loop:

    pr_review (gptme-contrib)     →   review-watch (gptme)
    ─────────────────────────         ─────────────────────
    AI reviews PR diff                AI acts as author
    Posts structured findings         Reads comments → fixes
    ReviewArtifact produced           ReviewArtifact consumed (optional)

``ReviewArtifact`` is the structured JSON handoff between the two stages.
When ``pr_review`` produces one, ``review-watch`` can consume it for
richer context (exact file/line, severity, confirmed/dropped status).

All fields are optional so the artifact degrades gracefully to the
plain-text comment path when not available.
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
        return cls(
            body=d.get("body", ""),
            file=d.get("file", ""),
            line=d.get("line"),
            severity=FindingSeverity(d.get("severity", FindingSeverity.WARNING.value)),
            status=FindingStatus(d.get("status", FindingStatus.OPEN.value)),
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
          "schema_version": 1,
          "pr": {"owner": "gptme", "repo": "gptme", "number": 1234},
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

    #: Schema version for forward-compatibility.
    schema_version: Literal[1] = 1

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
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: Path) -> None:
        """Persist the artifact to a JSON file."""
        path.write_text(self.to_json())

    @classmethod
    def from_dict(cls, d: dict) -> ReviewArtifact:
        pr = d.get("pr", {})
        return cls(
            pr_owner=pr.get("owner", ""),
            pr_repo=pr.get("repo", ""),
            pr_number=int(pr.get("number", 0)),
            findings=[ReviewFinding.from_dict(f) for f in d.get("findings", [])],
        )

    @classmethod
    def from_json(cls, text: str) -> ReviewArtifact:
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path) -> ReviewArtifact:
        """Load an artifact from a JSON file."""
        return cls.from_json(path.read_text())

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
